//! Advisory memory guidance from file sizes and read-only host telemetry.
//! No runtime is started and no admission/activation decision is made here.
use serde::Serialize;

const GIB: u64 = 1024 * 1024 * 1024;

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum MemoryStatus { Unknown, FilesExceedCapacity, FilesExceedAvailable, LimitedHeadroom, PlanningHeadroom, CapacityOnly }

#[derive(Clone, Debug, Serialize)]
pub struct MemoryReading {
    pub name: String,
    pub total_bytes: Option<u64>,
    pub available_bytes: Option<u64>,
    pub status: MemoryStatus,
}

#[derive(Clone, Debug, Serialize)]
pub struct MemoryPreflight {
    pub selected_file_bytes: Option<u64>,
    pub planning_bytes: Option<u64>,
    pub ram: MemoryReading,
    pub gpus: Vec<MemoryReading>,
    pub gpu_telemetry: String,
}

fn assessment(files: Option<u64>, guide: Option<u64>, total: Option<u64>, available: Option<u64>) -> MemoryStatus {
    let Some(files) = files else { return MemoryStatus::Unknown; };
    if total.is_some_and(|total| files > total) { return MemoryStatus::FilesExceedCapacity; }
    match available {
        Some(available) if files > available => MemoryStatus::FilesExceedAvailable,
        Some(available) => match guide {
            Some(guide) if guide <= available => MemoryStatus::PlanningHeadroom,
            Some(_) => MemoryStatus::LimitedHeadroom,
            None => MemoryStatus::Unknown,
        },
        None if total.is_some() => MemoryStatus::CapacityOnly,
        None => MemoryStatus::Unknown,
    }
}

fn reading(name: String, total: Option<u64>, available: Option<u64>, files: Option<u64>, guide: Option<u64>) -> MemoryReading {
    let total = total.filter(|value| *value > 0);
    let available = available.filter(|value| total.is_some_and(|total| *value <= total));
    MemoryReading { name, total_bytes: total, available_bytes: available, status: assessment(files, guide, total, available) }
}

fn estimate(sizes: &[Option<u64>], ram: (Option<u64>, Option<u64>), gpus: Vec<(String, Option<u64>)>) -> MemoryPreflight {
    let files = sizes.iter().try_fold(0u64, |sum, size| sum.checked_add((*size)?)).filter(|sum| *sum > 0);
    // Transparent planning margin, NOT a model allocation/fit prediction.
    let guide = files.and_then(|files| files.checked_add((files / 4).max(GIB)));
    let gpu_telemetry = if gpus.is_empty() { "unavailable" } else { "dedicated_capacity_only" }.to_string();
    MemoryPreflight { selected_file_bytes: files, planning_bytes: guide,
        ram: reading("Physical RAM".into(), ram.0, ram.1, files, guide),
        gpus: gpus.into_iter().map(|(name,total)| reading(name,total,None,files,guide)).collect(), gpu_telemetry }
}

pub fn inspect(sizes: &[Option<u64>]) -> MemoryPreflight {
    #[cfg(windows)]
    { estimate(sizes, windows_probe::ram(), windows_probe::gpus()) }
    #[cfg(not(windows))]
    { estimate(sizes, (None,None), Vec::new()) }
}

#[cfg(windows)]
mod windows_probe {
    use std::ffi::c_void;
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};

    pub(super) fn ram() -> (Option<u64>,Option<u64>) {
        // SAFETY: MEMORYSTATUSEX contains only integers; dwLength and the live
        // output buffer follow the documented GlobalMemoryStatusEx contract.
        // Audited: zero is valid for every integer field, sizeof fits dwLength,
        // and the API success result is checked before consuming output.
        // https://learn.microsoft.com/windows/win32/api/sysinfoapi/nf-sysinfoapi-globalmemorystatusex
        // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
        unsafe {
            let mut value: MEMORYSTATUSEX = std::mem::zeroed();
            value.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
            if GlobalMemoryStatusEx(&mut value) == 0 { return (None,None); }
            (Some(value.ullTotalPhys),Some(value.ullAvailPhys))
        }
    }

    #[repr(C)]
    struct Guid { a:u32, b:u16, c:u16, d:[u8;8] }
    #[repr(C)]
    struct UnknownVtable { query:usize, add_ref:usize, release:unsafe extern "system" fn(*mut c_void)->u32 }
    #[repr(C)]
    struct FactoryVtable { base:UnknownVtable, inherited:[usize;9], enumerate:unsafe extern "system" fn(*mut c_void,u32,*mut *mut c_void)->i32 }
    #[repr(C)]
    struct AdapterVtable { base:UnknownVtable, inherited:[usize;7], describe:unsafe extern "system" fn(*mut c_void,*mut AdapterDescription)->i32 }
    #[repr(C)]
    struct AdapterDescription { name:[u16;128],vendor:u32,device:u32,subsystem:u32,revision:u32,dedicated_video:usize,dedicated_system:usize,shared_system:usize,luid:[u32;2],flags:u32 }
    #[link(name="dxgi")]
    extern "system" { fn CreateDXGIFactory1(iid:*const Guid,result:*mut *mut c_void)->i32; }
    struct OwnedCom(*mut c_void);
    impl Drop for OwnedCom {
        fn drop(&mut self) {
            // SAFETY: Only successful DXGI-owned interface pointers construct
            // OwnedCom; IUnknown::Release is the third inherited vtable slot.
            // Audited: this private, non-Clone owner holds exactly one acquired
            // reference; factory/adapter scope keeps its vtable alive until Drop.
            // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
            unsafe { let table=*(self.0 as *const *const UnknownVtable); ((*table).release)(self.0); }
        }
    }

    pub(super) fn gpus() -> Vec<(String,Option<u64>)> {
        let mut result=Vec::new();
        // Native DXGI ABI; no new runtime/package or vendor subprocess. The
        // inherited slots match IDXGIFactory1 and IDXGIAdapter1, and the SDK
        // DXGI_ADAPTER_DESC1 layout is checked below. DedicatedVideoMemory is
        // total capacity, NEVER free VRAM or shared/integrated system memory.
        // https://learn.microsoft.com/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc1
        // SAFETY: bounded enumeration, validated HRESULT/non-null interfaces,
        // live output structures and RAII Release on every acquired interface.
        // Audited against SDK/generated Windows bindings: the IID is Factory1;
        // EnumAdapters1/GetDesc1 occupy slots 12/10; repr(C) uses the SDK integer,
        // SIZE_T and UTF-16 layouts. HRESULT/null checks precede dereferences,
        // UTF-16 reads stay within 128 units, and no borrowed pointer escapes.
        // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
        unsafe {
            let iid=Guid{a:0x770aae78,b:0xf26f,c:0x4dba,d:[0xa8,0x29,0x25,0x3c,0x83,0xd1,0xb3,0x87]};
            let mut pointer=std::ptr::null_mut();
            if CreateDXGIFactory1(&iid,&mut pointer)<0 || pointer.is_null() { return result; }
            let factory=OwnedCom(pointer);
            let table=*(factory.0 as *const *const FactoryVtable);
            for index in 0..16 {
                let mut pointer=std::ptr::null_mut();
                if ((*table).enumerate)(factory.0,index,&mut pointer)<0 || pointer.is_null() { break; }
                let adapter=OwnedCom(pointer);
                let table=*(adapter.0 as *const *const AdapterVtable);
                let mut description:AdapterDescription=std::mem::zeroed();
                if ((*table).describe)(adapter.0,&mut description)<0 || description.flags & 2 != 0 { continue; }
                let end=description.name.iter().position(|unit| *unit==0).unwrap_or(description.name.len());
                let name=String::from_utf16_lossy(&description.name[..end]);
                let name=if name.is_empty(){format!("Graphics adapter {index}")}else{name};
                result.push((name,(description.dedicated_video>0).then_some(description.dedicated_video as u64)));
            }
        }
        result
    }

    #[cfg(test)]
    #[test]
    fn dxgi_abi_matches_sdk_interface_slots_and_description() {
        assert_eq!(std::mem::offset_of!(FactoryVtable,enumerate),12*std::mem::size_of::<usize>());
        assert_eq!(std::mem::offset_of!(AdapterVtable,describe),10*std::mem::size_of::<usize>());
        assert_eq!(std::mem::offset_of!(AdapterDescription,dedicated_video),272);
        #[cfg(target_pointer_width="64")]
        assert_eq!(std::mem::size_of::<AdapterDescription>(),312);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn actual_selected_bytes_and_explicit_margin_drive_advisory_status() {
        let value=estimate(&[Some(2*GIB),Some(GIB)],(Some(16*GIB),Some(8*GIB)),Vec::new());
        assert_eq!(value.selected_file_bytes,Some(3*GIB));
        assert_eq!(value.planning_bytes,Some(4*GIB));
        assert_eq!(value.ram.status,MemoryStatus::PlanningHeadroom);
    }
    #[test]
    fn unknown_and_overflow_never_turn_into_zero_or_positive_fit() {
        for sizes in [vec![Some(GIB),None],vec![Some(u64::MAX),Some(1)],vec![]] {
            let value=estimate(&sizes,(Some(32*GIB),Some(16*GIB)),Vec::new());
            assert_eq!(value.selected_file_bytes,None);
            assert_eq!(value.ram.status,MemoryStatus::Unknown);
        }
        assert_eq!(estimate(&[Some(u64::MAX)],(None,None),Vec::new()).planning_bytes,None);
    }
    #[test]
    fn headroom_and_capacity_warnings_are_distinct_and_do_not_pool_vram() {
        let value=estimate(&[Some(6*GIB)],(Some(16*GIB),Some(7*GIB)),vec![("AMD card".into(),Some(4*GIB)),("NVIDIA card".into(),Some(4*GIB))]);
        assert_eq!(value.ram.status,MemoryStatus::LimitedHeadroom);
        assert!(value.gpus.iter().all(|gpu|gpu.status==MemoryStatus::FilesExceedCapacity));
        assert!(value.gpus.iter().all(|gpu|gpu.available_bytes.is_none()));
        assert_eq!(estimate(&[Some(6*GIB)],(Some(16*GIB),Some(5*GIB)),Vec::new()).ram.status,MemoryStatus::FilesExceedAvailable);
    }
    #[test]
    fn dedicated_capacity_never_becomes_a_free_memory_or_exact_fit_claim() {
        let value=estimate(&[Some(GIB)],(None,None),vec![("AMD Vulkan adapter".into(),Some(8*GIB)),("Shared adapter".into(),None)]);
        assert_eq!(value.gpus[0].status,MemoryStatus::CapacityOnly);
        assert_eq!(value.gpus[1].status,MemoryStatus::Unknown);
        assert_eq!(value.ram.status,MemoryStatus::Unknown);
        assert_eq!(value.gpu_telemetry,"dedicated_capacity_only");
    }
    #[test]
    fn host_probe_is_bounded_and_sanitizes_invalid_readings() {
        let value=inspect(&[Some(1024)]);
        println!("{}",serde_json::to_string(&value).unwrap());
        assert!(value.gpus.len()<=16);
        if let (Some(total),Some(free))=(value.ram.total_bytes,value.ram.available_bytes){assert!(total>0 && free<=total);}
        assert_eq!(reading("invalid".into(),Some(10),Some(11),Some(1),Some(2)).available_bytes,None);
    }
}

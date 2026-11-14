//! Windows Job Object with KILL_ON_JOB_CLOSE for child process trees.
//! Dropping the handle kills the whole tree, the only reliable orphan guard
//! on Windows.

use std::io;
use std::mem::{size_of, zeroed};

use windows_sys::Win32::Foundation::{CloseHandle, FALSE, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectBasicAccountingInformation,
    JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
    TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

pub struct ProcessJob {
    handle: HANDLE,
}

unsafe impl Send for ProcessJob {}
unsafe impl Sync for ProcessJob {}

impl ProcessJob {
    pub fn terminate(&self) -> io::Result<()> {
        // SAFETY: create_for transfers one valid job HANDLE into self; this
        // shared borrow keeps it open until the synchronous call returns.
        if unsafe { TerminateJobObject(self.handle, 1) } == 0 { // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn is_empty(&self) -> io::Result<bool> {
        // SAFETY: this Win32 C-layout accounting structure contains only
        // integer counters/times; all-zero bytes are valid for every field.
        let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { zeroed() }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
        // SAFETY: self owns the live job HANDLE. info is writable, correctly
        // aligned and exactly sized for JobObjectBasicAccountingInformation;
        // the optional returned-length pointer is NULL. Read only on success.
        let ok = unsafe { // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
            QueryInformationJobObject(
                self.handle,
                JobObjectBasicAccountingInformation,
                &mut info as *mut _ as *mut _,
                size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
                std::ptr::null_mut(),
            )
        };
        if ok == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(info.ActiveProcesses == 0)
    }

    pub fn create_for(pid: u32) -> io::Result<Self> {
        // SAFETY: NULL name/security attributes request an unnamed job with
        // default security. The zeroed C struct contains only valid zeroable
        // Win32 scalar fields; SetInformation receives its exact class/size.
        // Handles are checked before use. The temporary process HANDLE closes
        // once after assignment; every failure closes the job, and success
        // transfers its sole ownership to Self for Drop. No pointer escapes.
        unsafe { // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() || job == INVALID_HANDLE_VALUE {
                return Err(io::Error::last_os_error());
            }

            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const _,
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            if ok == 0 {
                let e = io::Error::last_os_error();
                CloseHandle(job);
                return Err(e);
            }

            let process = OpenProcess(PROCESS_TERMINATE | PROCESS_SET_QUOTA, FALSE, pid);
            if process.is_null() {
                let e = io::Error::last_os_error();
                CloseHandle(job);
                return Err(e);
            }

            let assign = AssignProcessToJobObject(job, process);
            CloseHandle(process);
            if assign == 0 {
                let e = io::Error::last_os_error();
                CloseHandle(job);
                return Err(e);
            }

            Ok(Self { handle: job })
        }
    }
}

impl Drop for ProcessJob {
    fn drop(&mut self) {
        if !self.handle.is_null() && self.handle != INVALID_HANDLE_VALUE {
            // SAFETY: self uniquely owns this checked job HANDLE, is not Clone,
            // and Drop runs once after all shared borrows end; no later use.
            unsafe { CloseHandle(self.handle) }; // nosemgrep: rust.lang.security.unsafe-usage.unsafe-usage
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;
    use std::time::{Duration, Instant};

    #[test]
    fn create_for_invalid_pid_errors() {
        match ProcessJob::create_for(0xFFFFFFFE) {
            Err(_) => {}
            Ok(_) => panic!("invalid pid must error"),
        }
    }

    #[test]
    fn drop_kills_assigned_process_tree() {
        let mut child = Command::new("cmd.exe")
            .args(["/C", "ping -n 30 127.0.0.1 > nul"])
            .spawn()
            .expect("spawn cmd.exe");

        let job = ProcessJob::create_for(child.id()).expect("create job");
        drop(job);

        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            match child.try_wait().expect("try_wait") {
                Some(_) => break,
                None if Instant::now() >= deadline => {
                    let _ = child.kill();
                    panic!("child survived 3s after ProcessJob drop");
                }
                None => std::thread::sleep(Duration::from_millis(50)),
            }
        }
    }
}

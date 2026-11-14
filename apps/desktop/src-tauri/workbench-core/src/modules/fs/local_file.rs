use crate::modules::workspace::{validate_wsl_distro_name, wsl_drvfs_to_windows, WorkspaceEnv};
use std::fs::File;
use std::os::windows::fs::MetadataExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle};
use windows_sys::Wdk::Foundation::OBJECT_ATTRIBUTES;
use windows_sys::Wdk::Storage::FileSystem::{NtCreateFile, FILE_OPEN, FILE_OPEN_REPARSE_POINT};
use windows_sys::Win32::Foundation::{OBJ_CASE_INSENSITIVE, OBJ_DONT_REPARSE, UNICODE_STRING};
use windows_sys::Win32::Storage::FileSystem::{
    GetDriveTypeW, GetFinalPathNameByHandleW, FILE_ATTRIBUTE_REPARSE_POINT, FILE_READ_ATTRIBUTES,
    FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE,
};
use windows_sys::Win32::System::WindowsProgramming::DRIVE_REMOTE;
use windows_sys::Win32::System::IO::IO_STATUS_BLOCK;

// Inspect the handle opened with no reparse traversal, never reopen its path
// for metadata or canonicalization. A check-then-follow would race renames.
pub(super) fn open(path: &str, workspace: &WorkspaceEnv) -> Result<File, String> {
    let (path, wsl) = match workspace {
        WorkspaceEnv::Local => (path.replace('/', "\\"), false),
        WorkspaceEnv::Wsl { distro } => {
            validate_wsl_distro_name(distro)?;
            if let Some(host) = wsl_drvfs_to_windows(path) {
                (host.to_string_lossy().into_owned(), false)
            } else {
                if !path.starts_with('/') || path.starts_with("//") || path.contains('\\') {
                    return Err("local WSL file links require an absolute distro path".into());
                }
                // Build the known local WSL provider path without resolve_path's
                // existence probe, which would already follow reparse points.
                (
                    format!(r"UNC\wsl.localhost\{distro}{}", path.replace('/', "\\")),
                    true,
                )
            }
        }
    };
    let bytes = path.as_bytes();
    if (!wsl && (bytes.len() < 3 || !bytes[0].is_ascii_alphabetic() || &bytes[1..3] != b":\\"))
        || path.contains('\0')
    {
        return Err("local file links require an absolute local drive path".into());
    }
    // Mapped SMB drives have no UNC prefix, but are not local either.
    if !wsl {
        let root: Vec<u16> = path[..3].encode_utf16().chain(Some(0)).collect();
        if unsafe { GetDriveTypeW(root.as_ptr()) } == DRIVE_REMOTE {
            return Err("network drives are not local file links".into());
        }
    }
    let mut name: Vec<u16> = format!(r"\??\{path}").encode_utf16().collect();
    let length = u16::try_from(name.len() * 2).map_err(|_| "file link is too long")?;
    let mut name = UNICODE_STRING {
        Length: length,
        MaximumLength: length,
        Buffer: name.as_mut_ptr(),
    };
    let attributes = OBJECT_ATTRIBUTES {
        Length: std::mem::size_of::<OBJECT_ATTRIBUTES>() as u32,
        ObjectName: &mut name,
        Attributes: OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE,
        ..Default::default()
    };
    let mut status = IO_STATUS_BLOCK::default();
    let mut handle = std::ptr::null_mut();
    // OBJ_DONT_REPARSE rejects intermediate reparse points; OPEN_REPARSE_POINT
    // opens a leaf reparse point itself, which the handle attributes reject.
    let result = unsafe {
        NtCreateFile(
            &mut handle,
            FILE_READ_ATTRIBUTES,
            &attributes,
            &mut status,
            std::ptr::null(),
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            FILE_OPEN,
            FILE_OPEN_REPARSE_POINT,
            std::ptr::null(),
            0,
        )
    };
    if result < 0 {
        return Err(format!(
            "cannot open local file without reparse traversal: {result:#x}"
        ));
    }
    // SAFETY: a successful NtCreateFile returns an owned handle.
    let file = unsafe { File::from_raw_handle(handle) };
    if file
        .metadata()
        .map_err(|e| e.to_string())?
        .file_attributes()
        & FILE_ATTRIBUTE_REPARSE_POINT
        != 0
    {
        return Err("reparse points are not local file links".into());
    }
    Ok(file)
}

pub(super) fn canonicalize(file: &File) -> Result<String, String> {
    let mut buffer = vec![0u16; 32768];
    let length = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            0,
        )
    } as usize;
    if length == 0 || length >= buffer.len() {
        return Err(std::io::Error::last_os_error().to_string());
    }
    decode_path(&buffer[..length])
}

// Security comparisons must never alias an unpaired UTF-16 filename to U+FFFD.
fn decode_path(path: &[u16]) -> Result<String, String> {
    let path = String::from_utf16(path).map_err(|_| "local file path is not valid UTF-16")?;
    Ok(super::strip_verbatim(&path))
}

#[cfg(test)]
mod path_tests {
    use super::*;

    #[test]
    fn canonical_paths_reject_unpaired_surrogates_instead_of_aliasing_names() {
        assert!(decode_path(&[0x43, 0x3a, 0x5c, 0xd800]).is_err());
        assert!(decode_path(&[0x43, 0x3a, 0x5c, 0xdc00]).is_err());
        let valid: Vec<u16> = r"\\?\C:\repo\asset.png".encode_utf16().collect();
        assert_eq!(decode_path(&valid).unwrap(), "C:/repo/asset.png");
    }
}

#[path="../../../apps/desktop/src-tauri/workbench-core/src/modules/proc/capture.rs"]
pub mod capture;

#[cfg(test)]
mod unix_tree_proof {
    use super::capture::{self, CancelHandle, Limits};
    use std::{fs, path::PathBuf, process::{Command, Stdio}, sync::Arc, thread, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};
    const LIMITS: Limits=Limits{stdout:1024,stderr:1024,stop_on_stdout_cap:false};
    fn fixture(name:&str)->PathBuf {
        let folder=std::env::var_os("JARVIS_UNIX_PROOF_OUTPUT").map(PathBuf::from).unwrap_or_else(std::env::temp_dir);
        fs::create_dir_all(&folder).unwrap();
        folder.join(format!("jarvis-proc-{}-{}.pids",name,SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()))
    }
    fn command(file:&PathBuf,mode:&str)->Command {
        let mut cmd=Command::new("python3");
        cmd.arg(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("owned_tree.py")).args([file.to_str().unwrap(),mode]).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped());
        cmd
    }
    fn pids(file:&PathBuf)->Vec<i32> {
        let deadline=Instant::now()+Duration::from_secs(5);
        loop {
            if let Ok(text)=fs::read_to_string(file) {
                let ids:Vec<i32>=text.split_whitespace().map(|n|n.parse().unwrap()).collect();
                if ids.len()==3 {return ids;}
            }
            assert!(Instant::now()<deadline,"owned child did not report PIDs");
            thread::sleep(Duration::from_millis(10));
        }
    }
    fn executing(pid:i32)->bool {
        fs::read_to_string(format!("/proc/{pid}/stat")).ok().and_then(|line|line.rsplit_once(") ").map(|(_,tail)|!tail.starts_with('Z'))).unwrap_or(false)
    }
    // CancelHandle is bound by production capture to the actually spawned
    // SharedChild/process group. Never choose a kill target from pidfile text.
    struct OwnedCaptureGuard(Arc<CancelHandle>);
    impl Drop for OwnedCaptureGuard {
        fn drop(&mut self){self.0.cancel();}
    }
    fn gone(ids:&[i32]) {
        let deadline=Instant::now()+Duration::from_secs(3);
        while ids.iter().any(|pid|executing(*pid)) {
            assert!(Instant::now()<deadline,"owned process still executing: {:?}",ids);
            thread::sleep(Duration::from_millis(10));
        }
        println!("owned root/child/grandchild terminated: {:?}",ids);
    }
    #[test]
    fn explicit_stop_kills_owned_python_child_and_grandchild() {
        let file=fixture("stop");let cmd=command(&file,"wait");
        let cancel=Arc::new(CancelHandle::default());let _cleanup=OwnedCaptureGuard(cancel.clone());let thread_cancel=cancel.clone();
        let task=thread::spawn(move||capture::run_tree_cancellable(&mut {cmd},Duration::from_secs(15),LIMITS,Some(&thread_cancel)).unwrap());
        let ids=pids(&file);
        assert!(ids.iter().all(|pid|executing(*pid)));
        for pid in &ids {assert_eq!(unsafe{libc::getpgid(*pid)},ids[0]);}
        cancel.cancel();let out=task.join().unwrap();assert!(out.cancelled);assert!(!out.timed_out);gone(&ids);
    }
    #[test]
    fn timeout_kills_owned_python_child_and_grandchild() {
        let file=fixture("timeout");
        let cancel=Arc::new(CancelHandle::default());let _cleanup=OwnedCaptureGuard(cancel.clone());
        let out=capture::run_tree_cancellable(&mut command(&file,"wait"),Duration::from_secs(2),LIMITS,Some(&cancel)).unwrap();
        let ids=pids(&file);assert!(out.timed_out);gone(&ids);
    }
    #[test]
    fn successful_root_exit_cleans_background_python_descendants() {
        let file=fixture("exit");
        let cancel=Arc::new(CancelHandle::default());let _cleanup=OwnedCaptureGuard(cancel.clone());
        let out=capture::run_tree_cancellable(&mut command(&file,"exit-root"),Duration::from_secs(10),LIMITS,Some(&cancel)).unwrap();
        let ids=pids(&file);assert_eq!(out.exit_code,Some(0));assert!(!out.timed_out);gone(&ids);
    }
}

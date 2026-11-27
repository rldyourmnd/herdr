use super::harness::*;

#[test]
fn agent_wait_exits_immediately_when_status_already_matches() {
    let base = unique_test_dir();
    let config_home = base.join("config");
    let runtime_dir = base.join("runtime");
    let socket_path = runtime_dir.join("herdr.sock");

    let herdr = spawn_herdr(&config_home, &runtime_dir, &socket_path);
    wait_for_socket(&socket_path, Duration::from_secs(5));

    let created = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_immediate_1","method":"workspace.create","params":{{"cwd":"{}","focus":true}}}}"#,
            base.display()
        ),
    );
    let workspace_id = created["result"]["workspace"]["workspace_id"]
        .as_str()
        .unwrap()
        .to_string();
    let pane_id = format!("{workspace_id}:p1");

    let reported = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_immediate_2","method":"pane.report_agent","params":{{"pane_id":"{}","source":"custom:test","agent":"pi","state":"idle"}}}}"#,
            pane_id
        ),
    );
    assert_eq!(reported["result"]["type"], "ok");

    let waited = run_cli(
        &socket_path,
        &[
            "agent",
            "wait",
            &pane_id,
            "--until",
            "idle",
            "--timeout",
            "1000",
        ],
    );
    assert!(
        waited.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&waited.stderr)
    );
    let waited_json: serde_json::Value = serde_json::from_slice(&waited.stdout).unwrap();
    assert_eq!(waited_json["result"]["agent"]["agent_status"], "idle");
    assert_eq!(waited_json["result"]["agent"]["agent"], "pi");

    let unsettled = run_cli(
        &socket_path,
        &[
            "agent",
            "wait",
            &pane_id,
            "--settle",
            "60000",
            "--timeout",
            "100",
        ],
    );
    assert!(!unsettled.status.success());
    assert!(unsettled.stderr.ends_with(b"\n"));
    let error: serde_json::Value = serde_json::from_slice(&unsettled.stderr).unwrap();
    assert_eq!(error["error"]["code"], "timeout");

    let settled = run_cli(
        &socket_path,
        &[
            "agent",
            "wait",
            &pane_id,
            "--settle",
            "25",
            "--timeout",
            "3000",
        ],
    );
    assert!(settled.status.success());
    assert!(settled.stdout.ends_with(b"\n"));
    let result: serde_json::Value = serde_json::from_slice(&settled.stdout).unwrap();
    assert_eq!(result["result"]["agent"]["agent_status"], "idle");

    cleanup_spawned_herdr(herdr, base);
}

#[test]
fn agent_wait_times_out_when_status_does_not_match() {
    let base = unique_test_dir();
    let config_home = base.join("config");
    let runtime_dir = base.join("runtime");
    let socket_path = runtime_dir.join("herdr.sock");

    let herdr = spawn_herdr(&config_home, &runtime_dir, &socket_path);
    wait_for_socket(&socket_path, Duration::from_secs(5));

    let created = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_timeout_1","method":"workspace.create","params":{{"cwd":"{}","focus":true}}}}"#,
            base.display()
        ),
    );
    assert_eq!(created["result"]["type"], "workspace_created");
    let pane_id = created["result"]["root_pane"]["pane_id"].as_str().unwrap();
    let reported = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_timeout_2","method":"pane.report_agent","params":{{"pane_id":"{}","source":"custom:test","agent":"pi","state":"working"}}}}"#,
            pane_id
        ),
    );
    assert_eq!(reported["result"]["type"], "ok");

    let waited = run_cli(
        &socket_path,
        &[
            "agent",
            "wait",
            pane_id,
            "--until",
            "blocked",
            "--timeout",
            "100",
        ],
    );
    assert!(!waited.status.success());
    assert!(
        String::from_utf8_lossy(&waited.stderr).contains("timed out waiting for agent status"),
        "stderr: {}",
        String::from_utf8_lossy(&waited.stderr)
    );

    cleanup_spawned_herdr(herdr, base);
}

#[test]
fn agent_wait_exits_when_done_status_matches() {
    let base = unique_test_dir();
    let config_home = base.join("config");
    let runtime_dir = base.join("runtime");
    let socket_path = runtime_dir.join("herdr.sock");
    let bin_dir = base.join("bin");

    fs::create_dir_all(&bin_dir).unwrap();
    let fake_pi = bin_dir.join("pi");
    fs::write(
        &fake_pi,
        "#!/bin/sh\nprintf 'starting\\n'\nsleep 4\nprintf 'Working...\\n'\nsleep 1\nprintf '\\033[2J\\033[Hdone\\n'\n",
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = fs::metadata(&fake_pi).unwrap().permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&fake_pi, perms).unwrap();
    }

    let inherited_path = std::env::var("PATH").unwrap_or_default();
    let path_override = format!("{}:{}", bin_dir.display(), inherited_path);
    let herdr = spawn_herdr_with_path(
        &config_home,
        &runtime_dir,
        &socket_path,
        Some(Path::new(&path_override)),
    );

    wait_for_socket(&socket_path, Duration::from_secs(5));

    let created = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_status_1","method":"workspace.create","params":{{"cwd":"{}","focus":true}}}}"#,
            base.display()
        ),
    );
    let workspace_id = created["result"]["workspace"]["workspace_id"]
        .as_str()
        .unwrap()
        .to_string();

    let tab_created = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_status_2","method":"tab.create","params":{{"workspace_id":"{}","focus":true}}}}"#,
            workspace_id
        ),
    );
    assert_eq!(tab_created["result"]["type"], "tab_created");

    let pane_id = created["result"]["root_pane"]["pane_id"]
        .as_str()
        .unwrap()
        .to_string();
    let start_pi = run_cli(&socket_path, &["pane", "run", &pane_id, "pi"]);
    assert!(start_pi.status.success());
    assert!(wait_until(
        Duration::from_secs(3),
        Duration::from_millis(25),
        || run_cli(&socket_path, &["agent", "get", &pane_id])
            .status
            .success()
    ));

    let waited = run_cli(
        &socket_path,
        &[
            "agent",
            "wait",
            &pane_id,
            "--until",
            "done",
            "--timeout",
            "10000",
        ],
    );
    assert!(
        waited.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&waited.stderr)
    );
    let waited_json: serde_json::Value = serde_json::from_slice(&waited.stdout).unwrap();
    assert_eq!(waited_json["result"]["agent"]["agent_status"], "done");
    assert_eq!(waited_json["result"]["agent"]["agent"], "pi");

    cleanup_spawned_herdr(herdr, base);
}

#[test]
fn agent_wait_ends_with_caller_exited_when_its_caller_exits() {
    let base = unique_test_dir();
    let config_home = base.join("config");
    let runtime_dir = base.join("runtime");
    let socket_path = runtime_dir.join("herdr.sock");

    let herdr = spawn_herdr(&config_home, &runtime_dir, &socket_path);
    wait_for_socket(&socket_path, Duration::from_secs(5));

    let created = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_caller_1","method":"workspace.create","params":{{"cwd":"{}","focus":true}}}}"#,
            base.display()
        ),
    );
    let pane_id = created["result"]["root_pane"]["pane_id"].as_str().unwrap();
    let reported = send_request(
        &socket_path,
        &format!(
            r#"{{"id":"req_cli_caller_2","method":"pane.report_agent","params":{{"pane_id":"{}","source":"custom:test","agent":"pi","state":"working"}}}}"#,
            pane_id
        ),
    );
    assert_eq!(reported["result"]["type"], "ok");

    // The shell is the caller: it starts the wait, lives long enough for the wait to see it,
    // and exits while the wait still runs.
    let error_path = base.join("wait.err");
    let caller = Command::new("sh")
        .arg("-c")
        .arg(r#""$0" agent wait "$1" --until idle --timeout 60000 >/dev/null 2>"$2" & sleep 2"#)
        .arg(env!("CARGO_BIN_EXE_herdr"))
        .arg(pane_id)
        .arg(&error_path)
        .env("HERDR_SOCKET_PATH", &socket_path)
        .status()
        .unwrap();
    assert!(caller.success());

    let started = std::time::Instant::now();
    let error = loop {
        let text = std::fs::read_to_string(&error_path).unwrap_or_default();
        if text.ends_with('\n') {
            break text;
        }
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "the wait outlived its caller"
        );
        std::thread::sleep(Duration::from_millis(50));
    };
    let error: serde_json::Value = serde_json::from_str(&error).unwrap();
    assert_eq!(error["error"]["code"], "caller_exited");

    cleanup_spawned_herdr(herdr, base);
}

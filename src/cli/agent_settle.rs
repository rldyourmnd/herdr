use std::io;
use std::sync::mpsc::{sync_channel, RecvTimeoutError};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::api::client::{ApiClient, ConnectionTarget};
use crate::api::schema::{
    AgentInfo, AgentStatus, EventsSubscribeParams, Method, Request, Subscription,
};

const ID: &str = "cli:agent:wait";

pub(super) fn wait(
    target: &str,
    mut until: Vec<AgentStatus>,
    timeout_ms: Option<u64>,
    settle_ms: u64,
) -> io::Result<i32> {
    let started = Instant::now();
    let deadline = timeout_ms.and_then(|ms| started.checked_add(Duration::from_millis(ms)));
    let settle = Duration::from_millis(settle_ms);
    if until.is_empty() {
        until.extend([AgentStatus::Idle, AgentStatus::Done, AgentStatus::Blocked]);
    }

    let get = super::agent::agent_get_request(target, ID);
    let initial = super::send_request(&get)?;
    if initial.get("error").is_some() {
        return super::print_response(&initial);
    }
    let identity = agent_info(&initial)?;
    let pane = &identity.pane_id;
    let client = super::target::api_client()?;
    // A saved-machine bridge serves one connection at a time. Keep the event
    // stream on its own bridge so the post-subscription snapshots can proceed.
    let subscription_bridge = super::target::subscription_bridge()?;
    let subscription_client = subscription_bridge.as_ref().map(|bridge| {
        ApiClient::for_target(ConnectionTarget::SocketPath(
            bridge.socket_path().to_owned(),
        ))
    });
    let mut stream = subscription_client
        .as_ref()
        .unwrap_or(&client)
        .stream(&Request {
            id: ID.into(),
            method: Method::EventsSubscribe(EventsSubscribeParams {
                subscriptions: vec![
                    Subscription::PaneAgentStatusChanged {
                        pane_id: pane.clone(),
                        agent_status: None,
                    },
                    Subscription::PaneAgentDetected {},
                    Subscription::PaneClosed {},
                    Subscription::PaneExited {},
                    Subscription::PaneMoved {},
                ],
            }),
        })
        .map_err(io::Error::other)?;
    let response = stream.next().map_err(io::Error::other)?;
    if response.get("error").is_some() {
        return super::print_response(&response);
    }
    let (sender, receiver) = sync_channel(64);
    std::thread::spawn(move || loop {
        let event = stream.next();
        let closed = event.is_err();
        if sender.send(event).is_err() || closed {
            break;
        }
    });

    let mut candidate = None;
    loop {
        // This read follows subscription, so new events cannot fall between the state read and
        // subscription. It also verifies that the original agent still owns the pane.
        let current = client.request_value(&get).map_err(io::Error::other)?;
        if current.get("error").is_some() {
            return not_running();
        }
        let agent = agent_info(&current)?;
        if !same_agent(&agent, &identity) {
            return not_running();
        }
        let now = Instant::now();
        let expires = candidate_deadline(&mut candidate, &agent, now, settle);
        if until.contains(&agent.agent_status)
            && (!is_ready(agent.agent_status) || expires.is_some_and(|time| now >= time))
        {
            return super::print_response(&current);
        }
        if deadline.is_some_and(|time| now >= time) {
            return super::print_response(&super::agent::cli_agent_error(
                ID,
                "timeout",
                "timed out waiting for agent status",
            ));
        }
        // If the current readiness is not one of the requested states, only an event or the
        // caller timeout can make progress. In particular this avoids rechecking expired timers.
        let settle_deadline = expires.filter(|_| until.contains(&agent.agent_status));
        let next = [deadline, settle_deadline].into_iter().flatten().min();
        loop {
            let event = match next {
                Some(time) => receiver.recv_timeout(time.saturating_duration_since(Instant::now())),
                None => receiver.recv().map_err(|_| RecvTimeoutError::Disconnected),
            };
            let event = match event {
                Ok(Ok(event)) => event,
                Ok(Err(error)) => return Err(io::Error::other(error)),
                Err(RecvTimeoutError::Timeout) => break,
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(io::Error::other("agent status subscription closed"))
                }
            };
            if event.get("error").is_some() {
                return super::print_response(&event);
            }
            let data = &event["data"];
            if data["previous_pane_id"].as_str() == Some(pane) {
                return not_running();
            }
            if data["pane_id"].as_str() != Some(pane) {
                continue;
            }
            match event["event"].as_str() {
                Some("pane_closed" | "pane_exited") => return not_running(),
                Some("pane_agent_detected") if data["released"].as_bool() == Some(true) => {
                    return not_running()
                }
                Some("pane_agent_detected") => break,
                Some("pane.agent_status_changed") => {
                    if !matches!(data["agent_status"].as_str(), Some("idle" | "done")) {
                        candidate = None;
                    }
                    break;
                }
                _ => {}
            }
        }
    }
}

fn is_ready(status: AgentStatus) -> bool {
    matches!(status, AgentStatus::Idle | AgentStatus::Done)
}

fn agent_info(response: &Value) -> io::Result<AgentInfo> {
    serde_json::from_value(response["result"]["agent"].clone()).map_err(io::Error::other)
}

fn same_agent(agent: &AgentInfo, initial: &AgentInfo) -> bool {
    agent.terminal_id == initial.terminal_id
        && agent.agent == initial.agent
        && initial
            .agent_session
            .as_ref()
            .is_none_or(|session| agent.agent_session.as_ref() == Some(session))
}

fn candidate_deadline(
    candidate: &mut Option<(u64, Instant)>,
    agent: &AgentInfo,
    now: Instant,
    settle: Duration,
) -> Option<Instant> {
    if !is_ready(agent.agent_status) {
        *candidate = None;
        return None;
    }
    let sequence = agent.state_change_seq;
    if candidate.is_none_or(|(current, _)| current != sequence) {
        *candidate = Some((sequence, now));
    }
    candidate.and_then(|(_, since)| since.checked_add(settle))
}

fn not_running() -> io::Result<i32> {
    super::print_response(&super::agent::cli_agent_error(
        ID,
        "agent_not_running",
        "agent stopped running while waiting for its status",
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn agent(status: AgentStatus, sequence: u64) -> AgentInfo {
        serde_json::from_value(serde_json::json!({
            "terminal_id": "t1", "agent_status": status, "workspace_id": "w1",
            "tab_id": "w1:t1", "pane_id": "w1:p1", "focused": false,
            "state_change_seq": sequence, "revision": 1,
        }))
        .unwrap()
    }

    #[test]
    fn idle_flash_cannot_complete_a_later_turn() {
        let start = Instant::now();
        let settle = Duration::from_secs(60);
        let mut candidate = None;
        assert_eq!(
            candidate_deadline(&mut candidate, &agent(AgentStatus::Idle, 1), start, settle),
            Some(start + settle)
        );
        let resumed = start + Duration::from_secs(2);
        assert_eq!(
            candidate_deadline(
                &mut candidate,
                &agent(AgentStatus::Working, 2),
                resumed,
                settle
            ),
            None
        );
        let done = start + Duration::from_secs(5);
        assert_eq!(
            candidate_deadline(&mut candidate, &agent(AgentStatus::Done, 3), done, settle),
            Some(done + settle)
        );
        assert_eq!(
            candidate_deadline(
                &mut candidate,
                &agent(AgentStatus::Idle, 3),
                done + Duration::from_secs(1),
                settle
            ),
            Some(done + settle)
        );
    }
}

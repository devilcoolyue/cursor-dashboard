//! Fixed instance and business operations; browser code never receives device tokens.
use super::{connection, identifier};
use serde::Deserialize;
use serde_json::{json, Value};

pub(super) fn request(
    app: &tauri::AppHandle,
    connection_id: Option<String>,
    method: &str,
    path: &str,
    body: Option<Value>,
) -> Result<Value, String> {
    if let Some(id) = connection_id {
        let id = identifier(Some(id))?;
        connection(app)?.request("POST", &format!("/native/connections/{id}/request"),
            Some(json!({"method": method, "path": path, "body": body})))
    } else {
        connection(app)?.request(method, path, body)
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum ConnectionOperation { List, Add, Select, Login, Disconnect, Remove }

#[tauri::command(rename_all = "snake_case")]
pub(super) async fn connection_request(
    app: tauri::AppHandle,
    operation: ConnectionOperation,
    connection_id: Option<String>,
    body: Option<Value>,
) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        use ConnectionOperation::*;
        let (method, route) = match operation {
            List => ("GET", "/native/connections".into()),
            Add => ("POST", "/native/connections".into()),
            Select => ("PUT", "/native/connections/active".into()),
            other => {
                let id = identifier(connection_id)?;
                match other {
                    Login => ("POST", format!("/native/connections/{id}/login")),
                    Disconnect => ("POST", format!("/native/connections/{id}/disconnect")),
                    Remove => ("DELETE", format!("/native/connections/{id}")),
                    _ => unreachable!(),
                }
            }
        };
        connection(&app)?.request(method, &route, body)
    }).await.map_err(|_| "Native instance operation failed")?
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub(super) enum ManageOperation {
    CreateWorkspace, DeleteWorkspace, Members, Invitations, Invite, RevokeInvitation,
    Role, RemoveMember, TransferOwner, Grants, Grant, RevokeGrant,
    Sessions, RevokeSession, Devices, RevokeDevice, Password, Users, UserState, InstanceAudit,
}

fn route(operation: ManageOperation, workspace: Option<String>, account: Option<String>, target: Option<String>)
    -> Result<(&'static str, String), String> {
    use ManageOperation::*;
    match operation {
        CreateWorkspace => return Ok(("POST", "/api/v1/workspaces".into())),
        Sessions => return Ok(("GET", "/api/v1/auth/sessions".into())),
        Devices => return Ok(("GET", "/api/v1/auth/devices".into())),
        Password => return Ok(("PUT", "/api/v1/auth/password".into())),
        Users => return Ok(("GET", "/api/v1/instance/users".into())),
        InstanceAudit => return Ok(("GET", "/api/v1/instance/audit".into())),
        RevokeSession | RevokeDevice | UserState => {
            let id = identifier(target)?;
            return Ok(match operation {
                RevokeSession => ("DELETE", format!("/api/v1/auth/sessions/{id}")),
                RevokeDevice => ("DELETE", format!("/api/v1/auth/devices/{id}")),
                _ => ("PUT", format!("/api/v1/instance/users/{id}")),
            });
        }
        _ => {}
    }
    let space = identifier(workspace)?;
    let base = format!("/api/v1/workspaces/{space}");
    Ok(match operation {
        DeleteWorkspace => ("DELETE", base),
        Members => ("GET", format!("{base}/members")),
        Invitations => ("GET", format!("{base}/invitations")),
        Invite => ("POST", format!("{base}/invitations")),
        TransferOwner => ("PUT", format!("{base}/owner")),
        Role | RemoveMember | RevokeInvitation => {
            let id = identifier(target)?;
            match operation {
                Role => ("PUT", format!("{base}/members/{id}")),
                RemoveMember => ("DELETE", format!("{base}/members/{id}")),
                _ => ("DELETE", format!("{base}/invitations/{id}")),
            }
        }
        Grants | Grant | RevokeGrant => {
            let id = identifier(account)?;
            let path = format!("{base}/accounts/{id}/grants");
            match operation {
                Grants => ("GET", path),
                other => {
                    let target = identifier(target)?;
                    (if matches!(other, Grant) { "PUT" } else { "DELETE" }, format!("{path}/{target}"))
                }
            }
        }
        _ => return Err("Unsupported remote operation".into()),
    })
}

#[tauri::command(rename_all = "snake_case")]
pub(super) async fn remote_manage(
    app: tauri::AppHandle, operation: ManageOperation, connection_id: String,
    workspace: Option<String>, account: Option<String>, target: Option<String>,
    offset: Option<u32>, body: Option<Value>,
) -> Result<Value, String> {
    let (method, mut path) = route(operation, workspace, account, target)?;
    if let Some(offset) = offset { path.push_str(&format!("?offset={offset}")); }
    tauri::async_runtime::spawn_blocking(move || request(&app, Some(connection_id), method, &path, body))
        .await.map_err(|_| "Remote request failed")?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn management_has_only_fixed_routes_and_uuid_resources() {
        assert!(serde_json::from_str::<ManageOperation>("\"device_switch\"").is_err());
        assert!(serde_json::from_str::<ConnectionOperation>("\"http_request\"").is_err());
        assert!(route(ManageOperation::RevokeDevice, None, None, Some("../auth/login".into())).is_err());
        assert!(route(ManageOperation::Members, Some("https://other.test".into()), None, None).is_err());
        let id = "00000000-0000-0000-0000-000000000001";
        assert_eq!(route(ManageOperation::RevokeDevice, None, None, Some(id.into())).unwrap(),
                   ("DELETE", format!("/api/v1/auth/devices/{id}")));
    }
}

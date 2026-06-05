from fastapi import HTTPException


def check_resource_guard(
    *,
    readonly: bool,
    deletable: bool,
    action: str,
) -> None:
    if action == "delete" and not deletable:
        raise HTTPException(status_code=403, detail="Built-in resource cannot be deleted")
    if action in ("update", "edit") and readonly:
        raise HTTPException(status_code=403, detail="Built-in resource is read-only")

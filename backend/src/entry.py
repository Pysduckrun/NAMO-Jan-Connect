from __future__ import annotations

import hmac
import secrets
import uuid
from urllib.parse import quote, unquote, urlparse

import asgi
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from workers import WorkerEntrypoint

from domain import CATEGORIES, TRANSITIONS, image_extension, iso_now, make_password_hash, normalize_phone, session_expiry, sla_expiry, token_hash, valid_email, verify_password
from email_client import send_smtp_email



class ApiError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def as_python(value):
    return value.to_py() if hasattr(value, "to_py") else value


def as_list(value):
    converted = as_python(value)
    return list(converted) if converted is not None else []


async def db_all(db, sql: str, *params) -> list[dict]:
    raw = await db.prepare(sql).bind(*params).raw(columnNames=True)
    rows = as_list(raw)
    if not rows:
        return []
    columns = [str(item) for item in as_list(rows[0])]
    return [dict(zip(columns, as_list(row))) for row in rows[1:]]


async def db_first(db, sql: str, *params) -> dict | None:
    rows = await db_all(db, sql, *params)
    return rows[0] if rows else None


async def db_run(db, sql: str, *params):
    return await db.prepare(sql).bind(*params).run()


def env_text(env, name: str, default: str = "") -> str:
    value = getattr(env, name, default)
    return str(value) if value is not None else default


def cors_headers(env, request_origin: str = "") -> dict[str, str]:
    allowed_origins = [item.strip() for item in env_text(env, "FRONTEND_URL", "http://localhost:5173").split(",") if item.strip()]
    if request_origin:
        is_allowed = (
            request_origin in allowed_origins
            or request_origin.startswith("http://localhost:")
            or request_origin.startswith("http://127.0.0.1:")
            or request_origin.startswith("https://localhost:")
            or (request_origin.startswith("https://") and (
                request_origin.endswith(".vercel.app") or
                request_origin.endswith(".pages.dev")
            ))
        )
        allowed_origin = request_origin if is_allowed else allowed_origins[0]
    else:
        allowed_origin = allowed_origins[0]
    return {
        "access-control-allow-origin": allowed_origin,
        "access-control-allow-credentials": "true",
        "access-control-allow-methods": "GET, POST, PATCH, OPTIONS",
        "access-control-allow-headers": "Authorization, Content-Type, x-demo-role",
        "access-control-max-age": "86400",
        "vary": "Origin",
    }


def json_response(env, payload, status: int = 200, request_origin: str = "") -> JSONResponse:
    headers = cors_headers(env, request_origin)
    headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return JSONResponse(payload, status_code=status, headers=headers)


def error_response(env, error: ApiError, request_origin: str = "") -> Response:
    return json_response(env, {"error": error.message, "detail": error.message}, error.status, request_origin)


def bearer_token(request) -> str | None:
    header = request.headers.get("authorization")
    if header and str(header).lower().startswith("bearer "):
        return str(header).split(" ", 1)[1].strip()
    cookie_header = request.headers.get("cookie", "")
    if cookie_header:
        for item in cookie_header.split(";"):
            item = item.strip()
            if item.startswith("njc_access_token="):
                return item.split("=", 1)[1].strip()
    return None


async def current_user(env, request) -> dict | None:
    token = bearer_token(request)
    if not token:
        return None
    return await db_first(
        env.DB,
        """SELECT u.id, u.name, u.email, u.phone, u.role, u.department_id AS departmentId
           FROM staff_sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=? AND s.expires_at > ? LIMIT 1""",
        token_hash(token), iso_now(),
    )


def require_role(user: dict | None, *roles: str) -> dict:
    if not user:
        raise ApiError(401, "Staff sign-in required")
    if user.get("role") not in roles:
        raise ApiError(403, "You do not have access to this portal")
    return user


COMPLAINT_SELECT = """
SELECT c.id, c.tracking_id AS trackingId, c.title, c.description,
       c.location_text AS location, c.latitude, c.longitude, c.category,
       c.status, c.priority, c.created_at AS createdAt, c.updated_at AS updatedAt,
       c.resolved_at AS resolvedAt, c.sla_due_at AS slaDueAt,
       d.name AS department, d.id AS departmentId,
       u.name AS citizenName, u.email AS citizenEmail, u.phone AS citizenPhone
FROM complaints c LEFT JOIN departments d ON d.id=c.department_id
LEFT JOIN users u ON u.id=c.citizen_id
"""


async def authenticate(env, request) -> Response:
    try:
        body = await request.json()
    except Exception as exc:
        raise ApiError(400, "Invalid JSON request") from exc
    identifier = str(body.get("identifier", "")).strip().lower()
    password = str(body.get("password", ""))
    if len(identifier) < 3 or len(password) < 8:
        raise ApiError(422, "Staff email and password are required")
    now = iso_now()
    if identifier == env_text(env, "ADMIN_EMAIL").lower() and hmac.compare_digest(password, env_text(env, "ADMIN_PASSWORD")):
        await db_run(env.DB, "INSERT INTO users (name,email,role,created_at) VALUES ('NJC Administrator',?,'admin',?) ON CONFLICT(email) DO UPDATE SET role='admin', name='NJC Administrator'", identifier, now)
        user = await db_first(env.DB, "SELECT id,name,email,role,department_id AS departmentId FROM users WHERE email=?", identifier)
        department_category = None
    else:
        portal = await db_first(env.DB, """SELECT dp.department_id AS departmentId, dp.portal_id AS portalId, dp.staff_email AS staffEmail,
            dp.password_salt AS passwordSalt, dp.password_hash AS passwordHash, d.name, d.category
            FROM department_portals dp JOIN departments d ON d.id=dp.department_id
            WHERE lower(dp.staff_email)=? LIMIT 1""", identifier)
        if not portal or not portal.get("passwordSalt") or not portal.get("passwordHash") or not verify_password(password, str(portal["passwordSalt"]), str(portal["passwordHash"])):
            raise ApiError(401, "Invalid staff credentials")
        email = str(portal["staffEmail"])
        await db_run(env.DB, """INSERT INTO users (name,email,role,department_id,created_at) VALUES (?,?,'department_staff',?,?)
            ON CONFLICT(email) DO UPDATE SET role='department_staff', department_id=excluded.department_id, name=excluded.name""", f"{portal['name']} Officer", email, portal["departmentId"], now)
        user = await db_first(env.DB, "SELECT id,name,email,role,department_id AS departmentId FROM users WHERE email=?", email)
        department_category = portal["category"]
    token = secrets.token_urlsafe(32)
    ttl = int(env_text(env, "SESSION_TTL_HOURS"))
    await db_run(env.DB, "INSERT INTO staff_sessions (user_id,token_hash,expires_at,created_at) VALUES (?,?,?,?)", user["id"], token_hash(token), session_expiry(ttl), now)
    res = json_response(env, {"access_token": token, "token_type": "bearer", "role": user["role"], "name": user["name"], "department_category": department_category})
    res.headers.append("set-cookie", f"njc_access_token={token}; Path=/; Max-Age={ttl * 3600}; SameSite=Lax")
    return res


async def complaint_get(env, request, query: dict[str, list[str]]) -> Response:
    scope = query.get("scope", ["stats"])[0]
    if scope == "stats":
        row = await db_first(env.DB, """SELECT COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN status='resolved' THEN 1 ELSE 0 END),0) AS resolved,
            COALESCE(SUM(CASE WHEN status NOT IN ('resolved','rejected') THEN 1 ELSE 0 END),0) AS active,
            COALESCE(ROUND(AVG(CASE WHEN status='resolved' THEN julianday(resolved_at) - julianday(created_at) ELSE NULL END), 1), 0) AS avgDays
            FROM complaints""") or {"total": 0, "resolved": 0, "active": 0, "avgDays": 0}
        return json_response(env, {"summary": row, "departments": []})
    if scope == "track":
        tracking = query.get("tracking", [""])[0].strip().upper()
        item = await db_first(env.DB, COMPLAINT_SELECT + " WHERE upper(c.tracking_id)=? LIMIT 1", tracking)
        if not item:
            raise ApiError(404, "Tracking ID not found")
        item["citizenName"] = "Citizen"
        item.pop("citizenEmail", None); item.pop("citizenPhone", None)
        
        history_rows = await db_all(env.DB, """
            SELECT h.old_status AS oldStatus, h.new_status AS newStatus, h.remarks,
                   h.changed_at AS changedAt, u.name AS changedByName, u.role AS changedByRole
            FROM status_history h
            LEFT JOIN users u ON u.id = h.changed_by
            WHERE h.complaint_id = ?
            ORDER BY h.changed_at ASC
        """, item["id"])
        
        for h in history_rows:
            role = h.pop("changedByRole")
            name = h.pop("changedByName")
            if role == "citizen":
                h["changedBy"] = "Citizen"
            elif role == "admin":
                h["changedBy"] = "System Administrator"
            elif role == "department_staff":
                h["changedBy"] = "Department Officer"
            else:
                h["changedBy"] = name or "System Router"
        item["history"] = history_rows
        return json_response(env, item)
    if scope == "gallery":
        parsed_url = urlparse(str(request.url))
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        rows = await db_all(env.DB, """SELECT c.id, c.tracking_id AS trackingId, c.title, c.location_text AS location,
            c.category, c.resolved_at AS resolvedAt, d.name AS department, a.object_key AS objectKey
            FROM complaints c JOIN departments d ON d.id=c.department_id
            JOIN complaint_attachments a ON a.complaint_id=c.id AND a.file_type='resolution_image'
            WHERE c.status='resolved' ORDER BY c.resolved_at DESC LIMIT 24""")
        for row in rows:
            row["imageUrl"] = f"{origin}/api/uploads/{quote(str(row.pop('objectKey')), safe='')}"
        return json_response(env, {"items": rows})
    user = await current_user(env, request)
    if scope == "admin":
        require_role(user, "admin")
        complaints = await db_all(env.DB, COMPLAINT_SELECT + " ORDER BY c.created_at DESC")
        history_rows = await db_all(env.DB, """
            SELECT h.complaint_id AS complaintId, h.old_status AS oldStatus, h.new_status AS newStatus,
                   h.remarks, h.changed_at AS changedAt, u.name AS changedBy
            FROM status_history h
            LEFT JOIN users u ON u.id = h.changed_by
            ORDER BY h.changed_at ASC
        """)
        history_map = {}
        for row in history_rows:
            cid = row.pop("complaintId")
            history_map.setdefault(cid, []).append(row)
        for c in complaints:
            c["history"] = history_map.get(c["id"], [])

        access = await db_all(env.DB, """SELECT dp.department_id AS departmentId, d.name AS department, d.category,
            dp.portal_id AS portalId, dp.staff_email AS staffEmail,
            CASE WHEN dp.password_hash IS NULL THEN 0 ELSE 1 END AS passwordConfigured FROM department_portals dp
            JOIN departments d ON d.id=dp.department_id ORDER BY dp.department_id""")
        return json_response(env, {"complaints": complaints, "departmentAccess": access})
    if scope == "department":
        staff = require_role(user, "department_staff", "admin")
        complaints = await db_all(env.DB, COMPLAINT_SELECT + (" ORDER BY c.created_at DESC" if staff["role"] == "admin" else " WHERE c.department_id=? ORDER BY c.created_at DESC"), *(() if staff["role"] == "admin" else (staff["departmentId"],)))
        history_rows = await db_all(env.DB, """
            SELECT h.complaint_id AS complaintId, h.old_status AS oldStatus, h.new_status AS newStatus,
                   h.remarks, h.changed_at AS changedAt, u.name AS changedBy
            FROM status_history h
            LEFT JOIN users u ON u.id = h.changed_by
            ORDER BY h.changed_at ASC
        """)
        history_map = {}
        for row in history_rows:
            cid = row.pop("complaintId")
            history_map.setdefault(cid, []).append(row)
        for c in complaints:
            c["history"] = history_map.get(c["id"], [])
            
        return json_response(env, {"complaints": complaints})
    if scope == "mine":
        user = await current_user(env, request)
        if user:
            complaints = await db_all(env.DB, COMPLAINT_SELECT + " WHERE c.citizen_id=? ORDER BY c.created_at DESC", user["id"])
            return json_response(env, {"complaints": complaints})
        return json_response(env, {"complaints": []})
    raise ApiError(400, "Unknown scope")


async def store_image(env, file, prefix: str) -> tuple[str, str]:
    content_type = str(getattr(file, "content_type", ""))
    try:
        extension = image_extension(content_type)
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
    max_bytes = int(env_text(env, "MAX_UPLOAD_MB", "5")) * 1024 * 1024
    contents = await file.read()
    if len(contents) > max_bytes:
        raise ApiError(422, f"Image exceeds the {env_text(env, 'MAX_UPLOAD_MB', '5')} MB limit")
    key = f"{prefix.strip('/')}/{uuid.uuid4().hex}{extension}"
    await env.UPLOADS.put(key, contents, httpMetadata={"contentType": content_type, "cacheControl": "public, max-age=31536000, immutable"})
    return key, content_type


async def complaint_create(env, request) -> Response:
    form = await request.form()
    name = str(form.get("citizenName") or "").strip()
    email = str(form.get("citizenEmail") or "").strip().lower()
    email_val = email if email else None
    try: phone = normalize_phone(str(form.get("citizenPhone") or ""))
    except ValueError as exc: raise ApiError(422, str(exc)) from exc
    category = str(form.get("category") or "")
    title = str(form.get("title") or "").strip()
    description = str(form.get("description") or "").strip()
    location = str(form.get("location") or "").strip()
    if len(name) < 2: raise ApiError(422, "Provide a valid name")
    if email_val and not valid_email(email_val): raise ApiError(422, "Provide a valid email")
    if category not in CATEGORIES or len(title) < 6 or len(description) < 20 or not location: raise ApiError(422, "Complete all complaint details")
    if email_val:
        recent = await db_first(env.DB, "SELECT COUNT(*) AS total FROM complaints c JOIN users u ON u.id=c.citizen_id WHERE lower(u.email)=? AND c.created_at >= datetime('now','-1 day')", email_val)
    else:
        recent = await db_first(env.DB, "SELECT COUNT(*) AS total FROM complaints c JOIN users u ON u.id=c.citizen_id WHERE u.phone=? AND c.created_at >= datetime('now','-1 day')", phone)
    if recent and int(recent["total"]) >= 10: raise ApiError(429, "Daily complaint limit reached")
    now = iso_now()
    if email_val:
        await db_run(env.DB, "INSERT INTO users (name,email,phone,role,created_at) VALUES (?,?,?,'citizen',?) ON CONFLICT(email) DO UPDATE SET name=excluded.name, phone=excluded.phone", name, email_val, phone, now)
        citizen = await db_first(env.DB, "SELECT id FROM users WHERE email=?", email_val)
        citizen_id = citizen["id"]
    else:
        res = await db_run(env.DB, "INSERT INTO users (name,email,phone,role,created_at) VALUES (?,NULL,?,'citizen',?)", name, phone, now)
        citizen_id = int(res.meta.last_row_id)
    department = await db_first(env.DB, "SELECT id,name,sla_days AS sla FROM departments WHERE category=?", category)
    result = await db_run(env.DB, """INSERT INTO complaints (tracking_id,citizen_id,category,department_id,title,description,location_text,latitude,longitude,status,priority,created_at,updated_at,sla_due_at)
        VALUES (?,?,?,?,?,?,?,?,?,'submitted','medium',?,?,?)""", f"pending-{uuid.uuid4().hex}", citizen_id, category, department["id"], title, description, location, form.get("latitude") or None, form.get("longitude") or None, now, now, sla_expiry(int(department["sla"])))
    complaint_id = int(result.meta.last_row_id)
    tracking_id = f"NJC-{now[:4]}-{complaint_id:06d}"
    await db_run(env.DB, "UPDATE complaints SET tracking_id=? WHERE id=?", tracking_id, complaint_id)
    await db_run(env.DB, "INSERT INTO status_history (complaint_id,old_status,new_status,remarks,changed_by,changed_at) VALUES (?,NULL,'submitted',?,?,?)", complaint_id, f"Complaint received and routed to {department['name']}.", citizen_id, now)
    for file in form.getlist("evidence")[:4]:
        if not getattr(file, "filename", ""): continue
        key, content_type = await store_image(env, file, f"complaints/{complaint_id}/evidence")
        await db_run(env.DB, "INSERT INTO complaint_attachments (complaint_id,object_key,file_type,content_type,uploaded_by,uploaded_at) VALUES (?,?,'evidence_image',?,?,?)", complaint_id, key, content_type, citizen_id, now)
    
    if email_val:
        subject = f"Complaint Filed Successfully - {tracking_id}"
        category_name = CATEGORIES.get(category, {}).get("name", category)
        body = (
            f"Dear {name},\n\n"
            f"Your complaint has been filed successfully on NAMO Jan Connect.\n\n"
            f"Tracking ID: {tracking_id}\n"
            f"Title: {title}\n"
            f"Category: {category_name}\n"
            f"Department: {department['name']}\n"
            f"Description: {description}\n\n"
            f"You can track the progress of your complaint here:\n"
            f"{env_text(env, 'FRONTEND_URL', 'http://localhost:5173').split(',')[0]}/citizen?tracking={tracking_id}\n\n"
            f"Regards,\n"
            f"NAMO Jan Connect Administration"
        )
        try:
            await send_smtp_email(env, email_val, subject, body)
        except Exception as e:
            print(f"Failed to send filing email: {e}")
            
    return json_response(env, {"ok": True, "trackingId": tracking_id, "department": department["name"]}, 201)



async def complaint_patch(env, request) -> Response:
    user = await current_user(env, request)
    content_type = str(request.headers.get("content-type") or "")
    if "application/json" in content_type:
        admin = require_role(user, "admin")
        body = await request.json()
        if body.get("action") != "assign_department" or not body.get("departmentId"): raise ApiError(400, "Invalid admin action")
        staff_email = str(body.get("staffEmail") or "").strip().lower()
        password = str(body.get("password") or "")
        if not valid_email(staff_email): raise ApiError(422, "A valid department staff email is required")
        try: password_salt, password_hash = make_password_hash(password)
        except ValueError as exc: raise ApiError(422, str(exc)) from exc
        department_id = int(body["departmentId"])
        await db_run(env.DB, "UPDATE department_portals SET staff_email=?, password_salt=?, password_hash=?, updated_at=? WHERE department_id=?", staff_email, password_salt, password_hash, iso_now(), department_id)
        await db_run(env.DB, "DELETE FROM staff_sessions WHERE user_id IN (SELECT id FROM users WHERE role='department_staff' AND department_id=?)", department_id)
        return json_response(env, {"ok": True, "changedBy": admin["name"]})
    actor = require_role(user, "department_staff", "admin")
    form = await request.form()
    complaint_id = int(str(form.get("complaintId") or "0"))
    new_status = str(form.get("status") or "")
    remarks = str(form.get("remarks") or "").strip()
    item = await db_first(env.DB, "SELECT id,status,department_id AS departmentId FROM complaints WHERE id=?", complaint_id)
    if not item: raise ApiError(404, "Complaint not found")
    if actor["role"] != "admin" and int(item["departmentId"]) != int(actor["departmentId"]): raise ApiError(403, "Complaint belongs to another department")
    if new_status not in TRANSITIONS.get(str(item["status"]), set()) or len(remarks) < 5: raise ApiError(422, "Invalid status transition or remark")
    now = iso_now(); resolved_at = now if new_status == "resolved" else None
    await db_run(env.DB, "UPDATE complaints SET status=?,updated_at=?,resolved_at=? WHERE id=?", new_status, now, resolved_at, complaint_id)
    await db_run(env.DB, "INSERT INTO status_history (complaint_id,old_status,new_status,remarks,changed_by,changed_at) VALUES (?,?,?,?,?,?)", complaint_id, item["status"], new_status, remarks, actor["id"], now)
    photos = form.getlist("resolutionPhoto")
    if new_status == "resolved":
        for photo in photos[:4]:
            if photo is not None and getattr(photo, "filename", ""):
                key, photo_type = await store_image(env, photo, f"complaints/{complaint_id}/resolution")
                await db_run(env.DB, "INSERT INTO complaint_attachments (complaint_id,object_key,file_type,content_type,uploaded_by,uploaded_at) VALUES (?,?,'resolution_image',?,?,?)", complaint_id, key, photo_type, actor["id"], now)
    
    info = await db_first(env.DB, """
        SELECT c.tracking_id AS trackingId, c.title, u.name, u.email
        FROM complaints c
        JOIN users u ON u.id = c.citizen_id
        WHERE c.id = ?
    """, complaint_id)
    if info and info.get("email"):
        status_labels = {
            "submitted": "Submitted",
            "acknowledged": "Acknowledged",
            "in_progress": "In Progress",
            "resolved": "Resolved",
            "rejected": "Rejected",
            "reopened": "Reopened",
        }
        status_label = status_labels.get(new_status, new_status.title())
        subject = f"Complaint Status Updated - {info['trackingId']}"
        body = (
            f"Dear {info['name']},\n\n"
            f"The status of your complaint '{info['title']}' has been updated to: {status_label}.\n\n"
            f"Remarks:\n{remarks}\n\n"
            f"You can track the live timeline here:\n"
            f"{env_text(env, 'FRONTEND_URL', 'http://localhost:5173').split(',')[0]}/citizen?tracking={info['trackingId']}\n\n"
            f"Regards,\n"
            f"NAMO Jan Connect Administration"
        )
        try:
            await send_smtp_email(env, str(info["email"]), subject, body)
        except Exception as e:
            print(f"Failed to send update email: {e}")

    return json_response(env, {"ok": True})


async def contact_create(env, request) -> Response:
    body = await request.json()
    name = str(body.get("name", "")).strip(); email = str(body.get("email", "")).strip().lower(); message = str(body.get("message", "")).strip()
    if len(name) < 2 or not valid_email(email) or len(message) < 10: raise ApiError(422, "Complete all contact fields")
    result = await db_run(env.DB, "INSERT INTO contact_messages (name,email,topic,tracking_id,message,status,created_at) VALUES (?,?,?,?,?,'new',?)", name, email, str(body.get("topic", "Support")), body.get("trackingId"), message, iso_now())
    
    ref = f"NJC-SUPPORT-{int(result.meta.last_row_id):06d}"
    subject = f"New NAMO Support Request: {ref}"
    body_text = (
        f"A new support message has been sent on NAMO Jan Connect.\n\n"
        f"Reference: {ref}\n"
        f"Name: {name}\n"
        f"Email: {email}\n"
        f"Topic: {body.get('topic', 'Support')}\n"
        f"Tracking ID: {body.get('trackingId', 'N/A')}\n\n"
        f"Message:\n{message}\n"
    )
    try:
        await send_smtp_email(env, "kushagra.singh0562@gmail.com", subject, body_text)
    except Exception as e:
        print(f"Failed to send support notification email: {e}")

    return json_response(env, {"ok": True, "reference": ref}, 201)


async def serve_upload(env, encoded_key: str) -> Response:
    key = unquote(encoded_key)
    if not key.startswith("complaints/") or ".." in key: raise ApiError(400, "Invalid object key")
    obj = await env.UPLOADS.get(key)
    if obj is None: raise ApiError(404, "File not found")
    headers = {"content-type": str(getattr(obj.httpMetadata, "contentType", "application/octet-stream")), "cache-control": "public, max-age=3600", "etag": str(obj.httpEtag)}
    content = bytes(as_python(await obj.arrayBuffer()))
    return Response(content=content, headers=headers)


app = FastAPI(
    title="NAMO Jan Connect API",
    version="1.0.0",
    description="Complaint routing, department portals, administration, D1 records, and R2 evidence.",
)


@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=cors_headers(request.scope["env"], request.headers.get("origin", "")))
    response = await call_next(request)
    for name, value in cors_headers(request.scope["env"], request.headers.get("origin", "")).items():
        response.headers[name] = value
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, error: ApiError):
    return error_response(request.scope["env"], error, request.headers.get("origin", ""))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin", "")
    headers = cors_headers(request.scope["env"], origin)
    headers["cache-control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return JSONResponse({"error": str(exc), "detail": str(exc)}, status_code=500, headers=headers)


@app.get("/api/health", tags=["System"])
async def health(request: Request):
    return json_response(request.scope["env"], {"status": "ok", "framework": "FastAPI", "database": "D1", "uploads": "R2"})


@app.post("/api/auth/login", tags=["Authentication"])
async def login(request: Request):
    return await authenticate(request.scope["env"], request)


@app.get("/api/auth/me", tags=["Authentication"])
async def auth_me(request: Request):
    env = request.scope["env"]
    return json_response(env, require_role(await current_user(env, request), "admin", "department_staff"))


@app.get("/api/complaints", tags=["Complaints"])
async def get_complaints(request: Request):
    query = {key: request.query_params.getlist(key) for key in request.query_params.keys()}
    return await complaint_get(request.scope["env"], request, query)


@app.post("/api/complaints", tags=["Complaints"])
async def create_complaint(request: Request):
    return await complaint_create(request.scope["env"], request)


@app.patch("/api/complaints", tags=["Complaints"])
async def update_complaint(request: Request):
    return await complaint_patch(request.scope["env"], request)


@app.post("/api/contact", tags=["Support"])
async def create_contact(request: Request):
    return await contact_create(request.scope["env"], request)


@app.get("/api/departments", tags=["System"])
async def get_departments(request: Request):
    env = request.scope["env"]
    depts = await db_all(env.DB, "SELECT id, name, category FROM departments ORDER BY id ASC")
    return json_response(env, {"departments": depts})


DEFAULT_OFFICERS = {
    "civic_infra": [
        {"id": "ci-1", "name": "Rajesh Kumar", "designation": "Chief Civil Engineer", "status": "active"},
        {"id": "ci-2", "name": "Anita Verma", "designation": "Sanitation Inspector", "status": "active"},
        {"id": "ci-3", "name": "Sunil Sharma", "designation": "Road & Drainage Supervisor", "status": "active"},
    ],
    "health_edu": [
        {"id": "he-1", "name": "Dr. Priya Patel", "designation": "Chief Medical Officer", "status": "active"},
        {"id": "he-2", "name": "Manoj Tiwari", "designation": "District Education Officer", "status": "active"},
    ],
    "law_order": [
        {"id": "lo-1", "name": "Inspector Vikram Singh", "designation": "Station House Officer", "status": "active"},
        {"id": "lo-2", "name": "Sub-Inspector Neha Rao", "designation": "Public Safety Coordinator", "status": "active"},
    ],
    "transport": [
        {"id": "tp-1", "name": "Amit Saxena", "designation": "Regional Transport Officer", "status": "active"},
        {"id": "tp-2", "name": "Kavita Nair", "designation": "Depot Transit Manager", "status": "active"},
    ],
    "employment_welfare": [
        {"id": "ew-1", "name": "Suresh Gupta", "designation": "Welfare Officer", "status": "active"},
        {"id": "ew-2", "name": "Pooja Mishra", "designation": "Social Security Coordinator", "status": "active"},
    ],
}


@app.get("/api/kv/officers/{category}", tags=["Officers"])
@app.get("/kv/officers/{category}", tags=["Officers"])
async def get_officers(request: Request, category: str):
    env = request.scope["env"]
    return json_response(env, DEFAULT_OFFICERS.get(category, []))


@app.post("/api/kv/officers/{category}", tags=["Officers"])
@app.post("/kv/officers/{category}", tags=["Officers"])
async def save_officers(request: Request, category: str):
    env = request.scope["env"]
    return json_response(env, {"ok": True})


@app.post("/api/auth/register", tags=["Authentication"])
async def register(request: Request):
    env = request.scope["env"]
    try:
        body = await request.json()
    except Exception as exc:
        raise ApiError(400, "Invalid JSON request") from exc
    email = str(body.get("email") or "").strip().lower()
    password = str(body.get("password") or "")
    try:
        department_id = int(body.get("departmentId") or 0)
    except ValueError:
        raise ApiError(400, "Invalid department ID")
        
    if not email or not password or not department_id:
        raise ApiError(400, "Email, password, and department are required")
        
    if not valid_email(email):
        raise ApiError(422, "A valid department staff email is required")
        
    if len(password) < 8:
        raise ApiError(422, "Password must be at least 8 characters long")
        
    # Check if department exists
    dept = await db_first(env.DB, "SELECT id FROM departments WHERE id=?", department_id)
    if not dept:
        raise ApiError(404, "Department not found")
        
    # Check if department already has credentials configured
    portal = await db_first(env.DB, "SELECT staff_email FROM department_portals WHERE department_id=?", department_id)
    if portal and portal.get("staff_email"):
        raise ApiError(409, "This department portal is already registered. Please contact the administrator.")
        
    try:
        password_salt, password_hash = make_password_hash(password)
    except ValueError as exc:
        raise ApiError(422, str(exc)) from exc
        
    await db_run(env.DB, "UPDATE department_portals SET staff_email=?, password_salt=?, password_hash=?, updated_at=? WHERE department_id=?", email, password_salt, password_hash, iso_now(), department_id)
    return json_response(env, {"ok": True, "message": "Registration successful. You can now sign in."})


@app.post("/api/test-email")
async def test_email(request: Request):
    body = await request.json()
    email = body.get("email")
    if not email:
        raise ApiError(400, "email is required")
    await send_smtp_email(request.scope["env"], email, "Test Connection", "SMTP connection works!")
    return JSONResponse({"status": "sent"})


@app.get("/api/uploads/{encoded_key:path}", tags=["Uploads"])
async def get_upload(request: Request, encoded_key: str):
    return await serve_upload(request.scope["env"], encoded_key)



class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)

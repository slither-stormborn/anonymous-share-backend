from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta, timezone
from supabase import create_client
from dotenv import load_dotenv
import os
import time

load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_SERVICE_ROLE")
BUCKET = os.getenv("SUPABASE_BUCKET", "files")

supabase = create_client(URL, KEY)

app = FastAPI()

# Allow all origins (frontend can be anywhere)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "FastAPI is running 👍"}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    content = await file.read()

    file_path = f"uploads/{int(time.time())}_{file.filename}"

    res = supabase.storage.from_(BUCKET).upload(
        file_path,
        content,
        {"content-type": "application/octet-stream"}
    )

    if isinstance(res, dict) and res.get("error"):
        return {"error": res["error"]["message"]}

    signed = supabase.storage.from_(BUCKET).create_signed_url(
        file_path,
        expires_in=3600,
        options={"download": True}
    )
    download_url = signed.get("signedUrl") or signed.get("signedURL")

    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    insert_res = supabase.table("file_uploads").insert({
        "file_name": file.filename,
        "file_path": file_path,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": expires_at.isoformat()
    }).execute()

    return {
        "message": "Uploaded successfully",
        "file_path": file_path,
        "download_url": download_url
    }



@app.get("/files")
def list_files():
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1️⃣ Fetch ALL files (not filtered), because we must clean expired ones
    all_files = supabase.table("file_uploads") \
        .select("*") \
        .execute().data

    # 2️⃣ CLEANUP: delete expired files from storage + DB
    for f in all_files:
        if f["expires_at"] < now_iso:
            # Delete from Supabase Storage
            try:
                supabase.storage.from_(BUCKET).remove(f["file_path"])
            except:
                pass  # ignore if already deleted or missing

            # Delete row from DB
            supabase.table("file_uploads") \
                .delete() \
                .eq("id", f["id"]) \
                .execute()

    # 3️⃣ After cleanup, fetch only valid non-deleted files
    res = supabase.table("file_uploads") \
        .select("*") \
        .filter("expires_at", "gt", now_iso) \
        .execute()

    files = res.data
    final_list = []

    # 4️⃣ Generate signed URLs for remaining files
    for f in files:
        file_path = f["file_path"]

        signed = supabase.storage.from_(BUCKET).create_signed_url(
            file_path,
            expires_in=3600,
            options={"download": True}
        )

        download_url = signed.get("signedUrl") or signed.get("signedURL")

        final_list.append({
            "id": f["id"],
            "file_name": f["file_name"],
            "file_path": file_path,
            "download_url": download_url,
            "uploaded_at": f["uploaded_at"],
            "expires_at": f["expires_at"]
        })

    return final_list

@app.delete("/cleanup")
def cleanup_expired_files():
    now_iso = datetime.now(timezone.utc).isoformat()

    # Get expired + not deleted files
    res = supabase.table("file_uploads") \
        .select("*") \
        .filter("is_deleted", "eq", False) \
        .filter("expires_at", "lt", now_iso) \
        .execute()

    expired_files = res.data
    deleted_list = []

    for f in expired_files:
        path = f["file_path"]

        # Delete from storage
        supabase.storage.from_(BUCKET).remove([path])

        # Mark deleted in DB
        supabase.table("file_uploads") \
            .update({"is_deleted": True}) \
            .eq("id", f["id"]) \
            .execute()

        deleted_list.append(path)

    return {
        "deleted_files": deleted_list,
        "count": len(deleted_list)
    }

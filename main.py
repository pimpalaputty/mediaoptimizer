import os
from fastapi import FastAPI, UploadFile, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from PIL import Image
import ffmpeg
import io
import uuid
from pathlib import Path
import zipfile
from typing import List
import shutil
import time
import asyncio
import json
from datetime import datetime

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create directories if they don't exist
UPLOAD_DIR = Path("uploads")
COMPRESSED_DIR = Path("compressed")
ZIP_DIR = Path("zips")
UPLOAD_DIR.mkdir(exist_ok=True)
COMPRESSED_DIR.mkdir(exist_ok=True)
ZIP_DIR.mkdir(exist_ok=True)

# Store compression progress
compression_progress = {}

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/robots.txt")
async def robots():
    return FileResponse("static/robots.txt", media_type="text/plain")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open("static/index.html") as f:
        return f.read()

async def compress_single_file(file: UploadFile, quality: int, task_id: str) -> dict:
    try:
        # Generate unique filename with original name
        original_filename = Path(file.filename).stem
        original_extension = Path(file.filename).suffix.lower()
        file_uuid = str(uuid.uuid4())
        
        # Use UUID as directory name to avoid filename conflicts
        file_dir = UPLOAD_DIR / file_uuid
        file_dir.mkdir(exist_ok=True)
        original_path = file_dir / f"{original_filename}{original_extension}"
        
        # Read and save file content
        content = await file.read()
        with open(original_path, "wb") as f:
            f.write(content)
        
        # Get original file size
        original_size = os.path.getsize(original_path)
        compression_progress[task_id]["total_size"] += original_size
        
        try:
            # Process based on file type
            if original_extension in ['.jpg', '.jpeg', '.png']:
                # Update status
                compression_progress[task_id]["current_file"] = file.filename
                compression_progress[task_id]["status"] = "Processing image"
                
                # Compress image
                img = Image.open(original_path)
                compressed_path = COMPRESSED_DIR / f"{original_filename}_compressed{original_extension}"
                if compressed_path.exists():
                    compressed_path = COMPRESSED_DIR / f"{original_filename}_compressed_{file_uuid}{original_extension}"
                img.save(compressed_path, quality=quality, optimize=True)
                
            elif original_extension in ['.mp4', '.mov', '.avi']:
                # Update status
                compression_progress[task_id]["current_file"] = file.filename
                compression_progress[task_id]["status"] = "Processing video"
                
                # Compress video
                compressed_path = COMPRESSED_DIR / f"{original_filename}_compressed.mp4"
                if compressed_path.exists():
                    compressed_path = COMPRESSED_DIR / f"{original_filename}_compressed_{file_uuid}.mp4"
                
                # Configure ffmpeg with progress updates
                process = await asyncio.create_subprocess_exec(
                    'ffmpeg',
                    '-i', str(original_path),
                    '-vcodec', 'libx264',
                    '-crf', str(int((100 - quality) * 0.51)),
                    '-acodec', 'aac',
                    str(compressed_path),
                    stderr=asyncio.subprocess.PIPE
                )
                
                # Monitor ffmpeg progress
                while True:
                    if process.stderr:
                        line = await process.stderr.readline()
                        if not line:
                            break
                        if b'time=' in line:
                            # Update progress for video processing
                            compression_progress[task_id]["status"] = "Processing video: " + line.decode().strip()
                    
                    await asyncio.sleep(0.1)
                
                await process.wait()
            else:
                return {
                    "error": f"Unsupported file type: {original_extension}",
                    "filename": file.filename
                }
            
            compressed_size = os.path.getsize(compressed_path)
            compression_progress[task_id]["processed_size"] += compressed_size
            compression_progress[task_id]["processed_files"] += 1
            
            return {
                "original_size": original_size,
                "compressed_size": compressed_size,
                "filename": file.filename,
                "compressed_path": str(compressed_path),
                "download_id": compressed_path.name
            }
        except Exception as e:
            compression_progress[task_id]["errors"].append(f"Error processing {file.filename}: {str(e)}")
            return {
                "error": str(e),
                "filename": file.filename
            }
        finally:
            # Clean up original file and directory
            if original_path.exists():
                original_path.unlink()
            if file_dir.exists():
                file_dir.rmdir()
    except Exception as e:
        compression_progress[task_id]["errors"].append(f"Error processing {file.filename}: {str(e)}")
        return {
            "error": str(e),
            "filename": file.filename
        }

@app.post("/compress-multiple")
async def compress_multiple(
    files: List[UploadFile],
    quality: int = Form(default=85)
):
    task_id = str(uuid.uuid4())
    compression_progress[task_id] = {
        "start_time": datetime.now().isoformat(),
        "total_files": len(files),
        "processed_files": 0,
        "total_size": 0,
        "processed_size": 0,
        "current_file": "",
        "status": "Starting compression",
        "errors": []
    }
    
    # Create a copy of the files list to avoid closed file handles
    file_copies = []
    for file in files:
        content = await file.read()
        # Create SpooledTemporaryFile with the content
        spooled_file = io.BytesIO(content)
        # Create UploadFile with the content and original filename
        file_copy = UploadFile(filename=file.filename, file=spooled_file)
        file_copies.append(file_copy)
    
    # Start compression in background task
    asyncio.create_task(process_files(file_copies, quality, task_id))
    
    return {"task_id": task_id}

async def process_files(files: List[UploadFile], quality: int, task_id: str):
    results = []
    for file in files:
        try:
            result = await compress_single_file(file, quality, task_id)
            results.append(result)
        finally:
            await file.close()  # Ensure file is closed after processing
    
    # Create zip file if there are successful compressions
    if any("compressed_path" in r for r in results):
        compression_progress[task_id]["status"] = "Creating ZIP file"
        zip_uuid = str(uuid.uuid4())
        zip_path = ZIP_DIR / f"compressed_files_{zip_uuid}.zip"
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for result in results:
                if "compressed_path" in result:
                    compressed_path = Path(result["compressed_path"])
                    zipf.write(compressed_path, compressed_path.name)
                    compressed_path.unlink()
        
        compression_progress[task_id]["status"] = "Completed"
        compression_progress[task_id]["zip_id"] = zip_path.name
    else:
        compression_progress[task_id]["status"] = "Completed with errors"

@app.get("/compression-progress/{task_id}")
async def get_compression_progress(task_id: str):
    if task_id not in compression_progress:
        return {"error": "Task not found"}
    return compression_progress[task_id]

@app.get("/download-zip/{zip_id}")
async def download_zip(zip_id: str):
    zip_path = ZIP_DIR / zip_id
    if not zip_path.exists():
        return {"error": "Zip file not found"}
    
    download_filename = "compressed_files.zip"
    if zip_id.startswith("compressed_files_"):
        download_filename = zip_id
    
    response = FileResponse(zip_path)
    response.headers["Content-Disposition"] = f"attachment; filename={download_filename}"
    return response

# Cleanup old files periodically
def cleanup_old_files():
    # Remove files older than 1 hour
    for directory in [UPLOAD_DIR, COMPRESSED_DIR, ZIP_DIR]:
        for file in directory.glob("*"):
            if file.is_file() and (time.time() - file.stat().st_mtime) > 3600:
                file.unlink()
    
    # Clean up old progress entries
    current_time = datetime.now()
    for task_id in list(compression_progress.keys()):
        start_time = datetime.fromisoformat(compression_progress[task_id]["start_time"])
        if (current_time - start_time).total_seconds() > 3600:
            del compression_progress[task_id] 
import os
from fastapi import FastAPI, UploadFile, Form, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from PIL import Image
import ffmpeg
import io
import uuid
from pathlib import Path
import zipfile
from typing import List, Dict, Optional
import shutil
import time
import asyncio
import json
from datetime import datetime
import logging
from concurrent.futures import ThreadPoolExecutor
import tempfile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Media Optimizer",
             description="A FastAPI application for optimizing images and videos",
             version="1.0.0")

# Enable CORS with more specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enable Gzip compression
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Create directories if they don't exist
UPLOAD_DIR = Path("uploads")
COMPRESSED_DIR = Path("compressed")
ZIP_DIR = Path("zips")
TEMP_DIR = Path("temp")

for directory in [UPLOAD_DIR, COMPRESSED_DIR, ZIP_DIR, TEMP_DIR]:
    directory.mkdir(exist_ok=True)

# Store compression progress
compression_progress: Dict[str, Dict] = {}

# Thread pool for CPU-intensive tasks
thread_pool = ThreadPoolExecutor(max_workers=os.cpu_count())

# Mount static files with caching headers
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/robots.txt")
async def robots():
    return FileResponse("static/robots.txt", media_type="text/plain")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    try:
        with open("static/index.html") as f:
            return f.read()
    except Exception as e:
        logger.error(f"Error reading index.html: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

async def optimize_image(img_path: Path, quality: int, output_path: Path) -> None:
    """Optimize image using Pillow with error handling."""
    try:
        with Image.open(img_path) as img:
            # Convert RGBA to RGB if necessary
            if img.mode == 'RGBA':
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])
                img = background
            
            # Optimize based on format
            if img_path.suffix.lower() in ['.jpg', '.jpeg']:
                img.save(output_path, 'JPEG', quality=quality, optimize=True)
            elif img_path.suffix.lower() == '.png':
                img.save(output_path, 'PNG', optimize=True, quality=quality)
    except Exception as e:
        logger.error(f"Error optimizing image {img_path}: {str(e)}")
        raise

async def compress_single_file(file: UploadFile, quality: int, task_id: str) -> dict:
    """Compress a single file with improved error handling and logging."""
    try:
        # Generate unique filename with original name
        original_filename = Path(file.filename).stem
        original_extension = Path(file.filename).suffix.lower()
        file_uuid = str(uuid.uuid4())
        
        # Use temporary directory for processing
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            original_path = temp_dir_path / f"{original_filename}{original_extension}"
            
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
                    compression_progress[task_id].update({
                        "current_file": file.filename,
                        "status": "Processing image"
                    })
                    
                    compressed_path = COMPRESSED_DIR / f"{original_filename}_compressed_{file_uuid}{original_extension}"
                    await optimize_image(original_path, quality, compressed_path)
                    
                elif original_extension in ['.mp4', '.mov', '.avi']:
                    compression_progress[task_id].update({
                        "current_file": file.filename,
                        "status": "Processing video"
                    })
                    
                    compressed_path = COMPRESSED_DIR / f"{original_filename}_compressed_{file_uuid}.mp4"
                    
                    # Configure ffmpeg with improved settings
                    process = await asyncio.create_subprocess_exec(
                        'ffmpeg',
                        '-i', str(original_path),
                        '-vcodec', 'libx264',
                        '-preset', 'medium',  # Balance between speed and compression
                        '-crf', str(int((100 - quality) * 0.51)),
                        '-acodec', 'aac',
                        '-movflags', '+faststart',  # Enable progressive download
                        str(compressed_path),
                        stderr=asyncio.subprocess.PIPE
                    )
                    
                    while True:
                        if process.stderr:
                            line = await process.stderr.readline()
                            if not line:
                                break
                            if b'time=' in line:
                                compression_progress[task_id]["status"] = f"Processing video: {line.decode().strip()}"
                        await asyncio.sleep(0.1)
                    
                    await process.wait()
                else:
                    raise ValueError(f"Unsupported file type: {original_extension}")
                
                compressed_size = os.path.getsize(compressed_path)
                compression_progress[task_id].update({
                    "processed_size": compression_progress[task_id]["processed_size"] + compressed_size,
                    "processed_files": compression_progress[task_id]["processed_files"] + 1
                })
                
                return {
                    "original_size": original_size,
                    "compressed_size": compressed_size,
                    "filename": file.filename,
                    "compressed_path": str(compressed_path),
                    "download_id": compressed_path.name
                }
                
            except Exception as e:
                logger.error(f"Error processing file {file.filename}: {str(e)}")
                compression_progress[task_id]["errors"].append(f"Error processing {file.filename}: {str(e)}")
                return {
                    "error": str(e),
                    "filename": file.filename
                }
                
    except Exception as e:
        logger.error(f"Error handling file {file.filename}: {str(e)}")
        compression_progress[task_id]["errors"].append(f"Error handling {file.filename}: {str(e)}")
        return {
            "error": str(e),
            "filename": file.filename
        }

@app.post("/compress-multiple")
async def compress_multiple(
    files: List[UploadFile],
    quality: int = Form(default=85)
):
    """Handle multiple file compression with improved error handling and progress tracking."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    if not 1 <= quality <= 100:
        raise HTTPException(status_code=400, detail="Quality must be between 1 and 100")
    
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
    
    # Create copies of files to avoid closed file handles
    file_copies = []
    for file in files:
        try:
            content = await file.read()
            file_copy = UploadFile(filename=file.filename, file=io.BytesIO(content))
            file_copies.append(file_copy)
        except Exception as e:
            logger.error(f"Error copying file {file.filename}: {str(e)}")
            compression_progress[task_id]["errors"].append(f"Error copying {file.filename}: {str(e)}")
    
    # Start compression in background task
    asyncio.create_task(process_files(file_copies, quality, task_id))
    
    return {"task_id": task_id}

async def process_files(files: List[UploadFile], quality: int, task_id: str):
    """Process multiple files asynchronously with improved error handling."""
    results = []
    for file in files:
        try:
            result = await compress_single_file(file, quality, task_id)
            results.append(result)
        except Exception as e:
            logger.error(f"Error in process_files for {file.filename}: {str(e)}")
            results.append({"error": str(e), "filename": file.filename})
        finally:
            await file.close()
    
    try:
        # Create zip file for successful compressions
        if any("compressed_path" in r for r in results):
            compression_progress[task_id]["status"] = "Creating ZIP file"
            zip_uuid = str(uuid.uuid4())
            zip_path = ZIP_DIR / f"compressed_files_{zip_uuid}.zip"
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for result in results:
                    if "compressed_path" in result:
                        compressed_path = Path(result["compressed_path"])
                        if compressed_path.exists():
                            zipf.write(compressed_path, compressed_path.name)
                            compressed_path.unlink()
            
            compression_progress[task_id].update({
                "status": "Completed",
                "zip_id": zip_path.name
            })
        else:
            compression_progress[task_id]["status"] = "Completed with errors"
    except Exception as e:
        logger.error(f"Error creating zip file for task {task_id}: {str(e)}")
        compression_progress[task_id].update({
            "status": "Error creating zip file",
            "errors": compression_progress[task_id]["errors"] + [str(e)]
        })

@app.get("/compression-progress/{task_id}")
async def get_compression_progress(task_id: str):
    """Get compression progress with error handling."""
    try:
        if task_id not in compression_progress:
            raise HTTPException(status_code=404, detail="Task not found")
        return compression_progress[task_id]
    except Exception as e:
        logger.error(f"Error getting progress for task {task_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/download-zip/{zip_id}")
async def download_zip(zip_id: str):
    """Download compressed files with error handling."""
    try:
        zip_path = ZIP_DIR / zip_id
        if not zip_path.exists():
            raise HTTPException(status_code=404, detail="Zip file not found")
        
        return FileResponse(
            zip_path,
            headers={
                "Content-Disposition": f"attachment; filename={zip_id}",
                "Cache-Control": "no-cache"
            }
        )
    except Exception as e:
        logger.error(f"Error downloading zip file {zip_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")

async def cleanup_old_files():
    """Cleanup old files and progress entries periodically."""
    while True:
        try:
            current_time = time.time()
            # Remove files older than 1 hour
            for directory in [UPLOAD_DIR, COMPRESSED_DIR, ZIP_DIR, TEMP_DIR]:
                for file in directory.glob("*"):
                    try:
                        if file.is_file() and (current_time - file.stat().st_mtime) > 3600:
                            file.unlink()
                    except Exception as e:
                        logger.error(f"Error removing file {file}: {str(e)}")
            
            # Clean up old progress entries
            current_datetime = datetime.now()
            for task_id in list(compression_progress.keys()):
                try:
                    start_time = datetime.fromisoformat(compression_progress[task_id]["start_time"])
                    if (current_datetime - start_time).total_seconds() > 3600:
                        del compression_progress[task_id]
                except Exception as e:
                    logger.error(f"Error cleaning up task {task_id}: {str(e)}")
            
            await asyncio.sleep(300)  # Run every 5 minutes
        except Exception as e:
            logger.error(f"Error in cleanup_old_files: {str(e)}")
            await asyncio.sleep(300)

@app.on_event("startup")
async def startup_event():
    """Initialize application state on startup."""
    asyncio.create_task(cleanup_old_files())

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on application shutdown."""
    thread_pool.shutdown(wait=True)
    # Clean up temporary files
    for directory in [UPLOAD_DIR, COMPRESSED_DIR, ZIP_DIR, TEMP_DIR]:
        try:
            shutil.rmtree(directory)
        except Exception as e:
            logger.error(f"Error cleaning up directory {directory}: {str(e)}") 
# Media Optimizer

A FastAPI-based web application for optimizing images and videos with a simple drag-and-drop interface.

## Features

- Image compression (JPEG, PNG)
- Video compression (MP4, MOV, AVI)
- Drag-and-drop file upload
- Real-time compression progress
- Batch processing
- Individual file processing
- Automatic file cleanup

## Deployment with Docker

### Prerequisites

- Docker
- Docker Compose
- Git

### Deployment Steps

1. Clone the repository:
   ```bash
   git clone <your-repo-url>
   cd mediaoptimizer
   ```

2. Build and start the containers:
   ```bash
   docker-compose up -d --build
   ```

3. Access the application:
   Open your browser and navigate to `http://your-server-ip:8000`

### Configuration

- The application runs on port 8000 by default
- Adjust `MAX_WORKERS` in docker-compose.yml based on your server's CPU cores
- Processed files are automatically cleaned up after 1 hour

### Directory Structure

- `/uploads` - Temporary storage for uploaded files
- `/compressed` - Temporary storage for compressed files
- `/zips` - Temporary storage for zip archives

### Monitoring

- View logs:
  ```bash
  docker-compose logs -f
  ```

- Check container status:
  ```bash
  docker-compose ps
  ```

### Updating

To update the application:

1. Pull the latest changes:
   ```bash
   git pull
   ```

2. Rebuild and restart the containers:
   ```bash
   docker-compose down
   docker-compose up -d --build
   ```

### Troubleshooting

- If the application is not accessible, check if the container is running:
  ```bash
  docker-compose ps
  ```

- Check the logs for errors:
  ```bash
  docker-compose logs -f
  ```

## Development

To run the application locally:

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   uvicorn main:app --reload
   ```

## Requirements

- Python 3.8+
- FFmpeg installed on your system

### Installing FFmpeg

#### macOS (using Homebrew):
```bash
brew install ffmpeg
```

#### Ubuntu/Debian:
```bash
sudo apt update
sudo apt install ffmpeg
```

## Setup

1. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

## Running the Application

1. Start the server:
```bash
uvicorn main:app --reload
```

2. Open your browser and navigate to:
```
http://localhost:8000
```

## Usage

1. Drag and drop an image or video file into the upload area, or click to select a file
2. Adjust the quality slider as needed (higher values = better quality but larger file size)
3. Click "Compress" to start the compression process
4. Once compression is complete, click "Download" to save the compressed file

## Notes

- Compressed files are stored in the `compressed` directory
- Original uploaded files are stored in the `uploads` directory
- Both directories are created automatically when the application starts
- The application supports common image formats (JPG, PNG) and video formats (MP4, MOV, AVI) 
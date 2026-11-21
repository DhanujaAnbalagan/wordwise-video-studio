
# VideoFlow AI – Prompt-Based Video Editing Engine

**IncuBrix Software Engineer Assessment – PS2**

VideoFlow AI is a prompt-based video editing application designed to translate natural-language editing instructions into structured timeline operations. The system aims to make video editing more accessible by allowing users to describe the changes they want instead of manually editing every section.

## Live Demo

- **Frontend:** https://videoflow-ai-nine.vercel.app/
- **GitHub Repository:** https://github.com/DhanujaAnbalagan/wordwise-video-studio
- **Frontend Hosting:** Vercel
- **Backend Hosting:** Local Docker deployment currently supported; separate public backend deployment is pending.

The live URL currently provides access to the frontend. Full backend integration and real-media processing should be validated separately.

---

## 1. Problem Statement

Traditional video editing requires users to manually identify timestamps, cut clips, arrange segments, adjust playback speed, and synchronize captions.

VideoFlow AI aims to simplify this process by accepting natural-language instructions such as:

- Remove the first 10 seconds.
- Keep only the section discussing a particular topic.
- Speed up a selected portion.
- Reorder selected segments.
- Add captions to the video.

The application translates editing instructions into structured operations that can be applied deterministically to a video timeline.

## 2. Key Features

### Natural-Language Editing

- Accepts editing instructions in natural language.
- Interprets instructions into structured timeline operations.
- Separates instruction interpretation from deterministic timeline execution.

### Timeline Operations

The planned and implemented operation model includes:

- Cut – Remove selected portions.
- Keep – Retain specified portions.
- Speed – Modify playback speed.
- Reorder – Change the order of selected segments.
- Caption – Add or manage captions.
- Overlay – Add visual overlays.

The actual availability of each operation depends on the implemented and tested API workflow.

### Transcription

- Integrates OpenAI Whisper for speech-to-text transcription.
- Supports timestamped transcript data.
- Uses word-level transcription data where available.
- Stores transcript information for editing workflows.

### Revision Management

- Maintains timeline revision history.
- Uses revision snapshots to track changes.
- Includes undo and redo logic in the backend design.
- Preserves the original source media.

### Rendering

- Uses FFmpeg for media processing.
- Builds deterministic render plans from timeline operations.
- Uses FFprobe for media validation.
- Separates timeline modification from final rendering.

### Storage

- Uses SQLite for development metadata and job tracking.
- Uses MinIO for S3-compatible media storage.
- Supports media upload and retrieval through the storage abstraction.
- Uses presigned URLs for object access.

### Background Jobs

- Supports background processing architecture.
- Includes job status tracking.
- Uses idempotency mechanisms to prevent duplicate job creation.
- Includes lease and heartbeat handling in the job-processing design.

### Reproducibility

The manifest design records information such as:

- Source and output file hashes.
- Applied editing operations.
- Processing settings.
- FFmpeg and FFprobe versions.
- Warnings and timestamps.

---

## 3. Technology Stack

| Component | Technology |
|---|---|
| Frontend | React 19 |
| Frontend Framework | TanStack Start |
| Styling | Tailwind CSS |
| Build Tool | Vite |
| Backend | Python, FastAPI |
| Media Processing | FFmpeg, FFprobe |
| Speech Recognition | OpenAI Whisper |
| Database | SQLite |
| Object Storage | MinIO |
| Testing | Pytest |
| Containerization | Docker, Docker Compose |
| Version Control | Git, GitHub |
| Frontend Hosting | Vercel |

---

## 4. System Architecture

The application follows a frontend-backend architecture.

```text
                 USER
                   |
                   v
          REACT FRONTEND
          TANSTACK START
                   |
                   v
             FASTAPI
              BACKEND
                   |
          +--------+---------+
          |        |         |
          v        v         v
       SQLite    Whisper   MinIO
          |        |         |
          |        v         |
          |   Transcript     |
          |                  |
          v                  v
      Timeline          Source Media
      Operations             |
          |                  |
          +--------+---------+
                   |
                   v
              RENDER PLAN
                   |
                   v
                 FFMPEG
                   |
                   v
             EDITED VIDEO
                   |
                   v
             OUTPUT STORAGE
```

The frontend is deployed independently from the backend. The backend requires its own hosting environment with FFmpeg, storage, database persistence, and sufficient processing resources.

---

## 5. Project Structure

```text
wordwise-video-studio/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── media/
│   │   ├── models/
│   │   ├── services/
│   │   └── main.py
│   │
│   ├── tests/
│   ├── requirements.txt
│   └── Dockerfile
│
├── src/
│   ├── components/
│   ├── lib/
│   ├── routes/
│   ├── start.ts
│   └── server.ts
│
├── public/
│   └── demo/
│
├── docker-compose.yml
├── vite.config.ts
├── package.json
├── bun.lock
├── .env.example
├── README.md
├── REPORT.md
├── SOURCES.md
└── AI_USE.md
```

Directory contents may evolve as the project develops.

---

## 6. Prerequisites

Install the following tools:

- Node.js
- Bun
- Python 3.10 or later
- Docker Desktop
- Docker Compose
- FFmpeg
- FFprobe
- Git

Whisper is an optional local dependency for running actual transcription.

Install Whisper using:

```bash
pip install -U openai-whisper
```

Make sure FFmpeg and FFprobe are available in your system PATH.

Verify:

```bash
ffmpeg -version
ffprobe -version
```

---

## 7. Local Setup

### Step 1: Clone the Repository

```bash
git clone https://github.com/DhanujaAnbalagan/wordwise-video-studio.git
```

```bash
cd wordwise-video-studio
```

### Step 2: Install Frontend Dependencies

```bash
bun install
```

### Step 3: Configure Environment Variables

Create a `.env` file using the example configuration:

```bash
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
Copy-Item .env.example .env
```

Review the environment variables before starting the application.

### Step 4: Install Backend Dependencies

Create a Python virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r backend/requirements.txt
```

### Step 5: Start Backend Services

```bash
docker compose up -d --build
```

Check running containers:

```bash
docker compose ps
```

View backend logs:

```bash
docker compose logs -f backend
```

### Step 6: Start the Frontend

In a separate terminal:

```bash
bun run dev
```

Open the local frontend URL displayed by the development server.

---

## 8. Docker Compose

Docker Compose is used to manage the backend and supporting storage services.

Start:

```bash
docker compose up -d --build
```

Check status:

```bash
docker compose ps
```

View backend logs:

```bash
docker compose logs -f backend
```

Stop services:

```bash
docker compose down
```

Stop services and remove development volumes:

```bash
docker compose down -v
```

**Warning:** The last command deletes Docker volumes and can remove locally stored development data. Do not run it when you need to preserve uploaded media or database contents.

---

## 9. Environment Configuration

The `.env.example` file documents the backend configuration.

Example:

```dotenv
ENV=development

DATABASE_URL=sqlite:///./data/videoflow.db

DATA_DIR=./data

STORAGE_BACKEND=minio

MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=admin
MINIO_SECRET_KEY=password
MINIO_BUCKET=videoflow

FFMPEG_PATH=ffmpeg
FFPROBE_PATH=ffprobe

WHISPER_MODEL=base
WHISPER_DEVICE=cpu
WHISPER_TIMEOUT_S=300

JOB_TIMEOUT_S=3600
JOB_LEASE_TIMEOUT_S=300

IDEMPOTENCY_TTL_HOURS=24
```

Use development credentials only for local testing.

For deployment:

- Replace default MinIO credentials.
- Configure persistent storage.
- Set production database credentials.
- Keep secrets outside GitHub.
- Configure the frontend with the public backend API URL.
- Never commit passwords, API keys, or access tokens.

---

## 10. Vercel Frontend Deployment

The React and TanStack Start frontend is deployed on Vercel.

**Live URL:**

https://videoflow-ai-nine.vercel.app/

### Deployment Steps

1. Push the latest project changes to GitHub.
2. Open https://vercel.com/
3. Select Add New Project.
4. Import the GitHub repository:

   `DhanujaAnbalagan/wordwise-video-studio`

5. Configure the frontend project.

| Setting | Value |
|---|---|
| Project Name | videoflow-ai |
| Framework Preset | TanStack Start |
| Root Directory | `./` |
| Production Branch | `main` |
| Install Command | Automatically detected |
| Build Command | Automatically detected |
| Output Directory | Automatically detected |

6. Review the build settings.
7. Add environment variables only when required by the frontend configuration.
8. Click Deploy.
9. Wait for the production deployment to finish.
10. Open the generated deployment URL and verify the application.

### Subsequent Deployments

Whenever new changes are pushed to the `main` branch, Vercel can automatically trigger a new deployment when Git integration is enabled.

### Frontend and Backend Connection

The frontend and backend are separate services.

For a complete deployed workflow:

1. Deploy the FastAPI backend to a suitable server.
2. Install and configure FFmpeg and FFprobe on that server.
3. Configure persistent database and media storage.
4. Configure the frontend with the backend's public API URL.
5. Configure CORS to allow the Vercel frontend domain.
6. Redeploy the frontend after changing its environment variables.
7. Test API connectivity and real-media workflows.

**Deployment status:** The frontend is publicly deployed. A separate public backend deployment and full end-to-end production integration remain to be completed and verified.

---

## 11. Backend API

The backend uses FastAPI and provides API documentation through OpenAPI when the server is running.

Local backend address:

```text
http://127.0.0.1:8000
```

Common documentation endpoint:

```text
http://127.0.0.1:8000/docs
```

The API implementation includes project/session management, instruction processing, and revision tracking.

The broader system design also includes endpoints for media upload, transcription, rendering, and background job tracking. Check the live OpenAPI specification before relying on any endpoint or request format.

### Example Instruction

An example editing instruction:

```text
Remove the first 10 seconds of the video.
```

The intended structured operation is:

```json
{
  "type": "cut",
  "start_ms": 0,
  "end_ms": 10000
}
```

The backend instruction test successfully applied a 10-second cut operation and created a new revision.

This is a timeline-operation test and does not by itself prove successful rendering of a real video.

---

## 12. Media Processing Pipeline

The intended processing workflow is:

```text
Video Upload
     |
     v
Media Storage
     |
     v
Whisper Transcription
     |
     v
Timestamped Transcript
     |
     v
Natural-Language Instruction
     |
     v
Structured Timeline Operation
     |
     v
Timeline Revision
     |
     v
Render Plan Generation
     |
     v
FFmpeg Processing
     |
     v
FFprobe Validation
     |
     v
Output Storage
     |
     v
Rendered Video
```

The original source media is preserved. Timeline operations are designed to modify the editing representation rather than overwrite the original input.

---

## 13. Testing

The backend uses Pytest.

Run the standard test suite:

```bash
pytest -q
```

Run the integration suite when the required Docker and MinIO services are available:

```bash
INTEGRATION=1 pytest -q
```

The project documentation records the following historical test runs:

| Test suite | Result |
|---|---|
| Default test suite | 235 passed, 3 skipped |
| MinIO integration suite | 238 passed |

The additional integration tests documented include:

- MinIO connection testing.
- Media upload and download testing.
- Upload, render and retrieve workflow testing.

These are recorded test results. Re-run the tests against the latest repository version before using them as final assessment evidence.

### Additional Verification

Check Git whitespace errors:

```bash
git diff --check
```

Build the frontend:

```bash
bun run build
```

---

## 14. Known Limitations

- The public frontend deployment and backend deployment are separate.
- Full end-to-end integration in a public deployment requires further validation.
- Whisper must be installed and configured for actual transcription; some tests use mocks.
- MP4 is the currently documented output format.
- Real-world rendering accuracy and processing latency require additional measured tests.
- Caption synchronization must be validated against real video inputs.
- Production deployment requires secure credentials and persistent storage.
- Cost and licensing metadata are not currently included in the reproducibility manifest.
- More scalable distributed job processing can be considered for future improvements.

---

## 15. Future Improvements

- Complete public backend deployment.
- Improve frontend-backend integration.
- Expand supported media formats and codecs.
- Add more comprehensive real-media scenario testing.
- Improve caption synchronization and rendering validation.
- Add measured instruction-processing and rendering performance metrics.
- Improve production monitoring and error reporting.
- Introduce scalable distributed job workers.
- Add cost and licensing information to reproducibility manifests.

---

## 16. AI-Assisted Development Disclosure

AI tools were used as development assistance during the implementation process.

### AI Assistance

- ChatGPT: implementation guidance, debugging, code review, algorithm discussions, documentation and technical explanations.
- Google Antigravity: AI-assisted development and code modification.

### Models and Processing Tools

- OpenAI Whisper: speech transcription component.
- FFmpeg: video and audio processing.
- FFprobe: media validation.

### Development and Infrastructure Tools

- Python
- FastAPI
- React
- TanStack Start
- SQLite
- MinIO
- Docker
- Pytest
- Git
- GitHub
- Vercel

AI assistance and manually performed work should be distinguished in the assessment disclosure. All actual tools, models, APIs, datasets, subscriptions and costs should be reported accurately.

---

## 17. Repository

GitHub:

https://github.com/DhanujaAnbalagan/wordwise-video-studio

The repository contains the frontend, backend, configuration, tests and project documentation.

---

## 18. Author

**VideoFlow AI**

IncuBrix Software Engineer Assessment

**Problem Statement:** PS2 – Prompt-Based Video Editing Engine

**Application Track:** Software Engineer

---

*This README describes the project architecture, documented implementation and deployment status. Features and performance claims should be considered verified only when supported by reproducible tests and accessible evidence.*

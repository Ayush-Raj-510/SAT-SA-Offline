# SAT-SA --- Supervisory Analytics Tool for SOC Assessment

SAT-SA is a Dockerized web application for SOC assessment and
supervisory analytics.

## 1. Prerequisite

Install **Docker Desktop**:

**Download:**\
https://www.docker.com/products/docker-desktop/

**Windows installation guide:**\
https://docs.docker.com/desktop/setup/install/windows-install/

After installation, open Docker Desktop and make sure it is running.

You do **not** need to install Node.js, Python, PostgreSQL, or Ollama
separately.

------------------------------------------------------------------------

# 2. Run SAT-SA Locally

Extract the SAT-SA ZIP file and open **PowerShell** inside the project
folder.

Make sure the folder contains:

``` text
docker-compose.yml
```

## Step 1 --- Stop any previous SAT-SA containers

``` powershell
docker compose down
```

## Step 2 --- Enable Ollama AI

Run:

``` powershell
$env:OLLAMA_ENABLED="true"
```

> **Important:** This command enables the AI Analyst for the current
> PowerShell window.
>
> If you close PowerShell and open a new PowerShell window, run this
> command again before starting SAT-SA.

## Step 3 --- Build and start the complete application

``` powershell
docker compose --profile ai up -d --build
```

This starts:

-   Frontend
-   Backend
-   PostgreSQL
-   Ollama
-   AI initialization service

## Step 4 --- Download the AI model

Run this **only the first time**:

``` powershell
docker compose exec ollama ollama pull llama3.2:3b
```

The model download can take some time because the model is large.

Wait until the command finishes successfully.

## Step 5 --- Check that the model is installed

``` powershell
docker compose exec ollama ollama list
```

You should see:

``` text
llama3.2:3b
```

## Step 6 --- Restart the backend

``` powershell
docker compose restart backend
```

## Step 7 --- Check all containers

``` powershell
docker compose ps
```

The main services should be running.

## Step 8 --- Open the website

Open:

**http://localhost:3001**

Go to **AI Analyst** to test the local AI.

------------------------------------------------------------------------

# 3. First-Time Setup --- Important

The **first time** you run the project, it can take some time.

Docker may need to:

-   Download Docker images
-   Build the frontend and backend
-   Start PostgreSQL
-   Start Ollama
-   Download `llama3.2:3b`

The first AI question can also take a few minutes, especially on a
CPU-only computer, because the model needs to load into memory.

You may see:

``` text
Model is reading the snapshot... first tokens take a while on CPU.
```

**This is normal.**

Do not stop the request immediately. Wait for the first response.

After the initial setup, starting SAT-SA is much faster.

------------------------------------------------------------------------

# 4. Run SAT-SA Again Later

After the first setup, you do not need to build or download the model
again.

Open a new PowerShell window in the project folder.

Run:

``` powershell
$env:OLLAMA_ENABLED="true"
```

Then:

``` powershell
docker compose --profile ai up -d
```

Open:

**http://localhost:3001**

------------------------------------------------------------------------

# 5. Stop the Application

``` powershell
docker compose --profile ai down
```

This stops the SAT-SA containers.

Your Docker volumes are not removed by this command, so the downloaded
Ollama model can remain available.

------------------------------------------------------------------------

# 6. Useful Commands

### Check containers

``` powershell
docker compose ps
```

### View all logs

``` powershell
docker compose logs --tail=50
```

### Backend logs

``` powershell
docker compose logs backend --tail=50
```

### Ollama logs

``` powershell
docker compose logs ollama --tail=50
```

### Follow backend logs

``` powershell
docker compose logs -f backend
```

### Follow Ollama logs

``` powershell
docker compose logs -f ollama
```

### Check installed AI model

``` powershell
docker compose exec ollama ollama list
```

------------------------------------------------------------------------

# 7. Troubleshooting

## AI Analyst says `model 'llama3.2:3b' not found`

Run:

``` powershell
docker compose exec ollama ollama pull llama3.2:3b
```

Then:

``` powershell
docker compose restart backend
```

## AI Analyst is disabled

Make sure you ran:

``` powershell
$env:OLLAMA_ENABLED="true"
```

Then recreate the backend:

``` powershell
docker compose up -d --force-recreate backend
```

## Website does not open

Check:

``` powershell
docker compose ps
```

Then:

``` powershell
docker compose logs --tail=50
```

## First AI response is very slow

This can happen during the first local inference, especially when using
CPU.

Wait for the model to finish loading.

If it is still stuck after several minutes, check:

``` powershell
docker compose logs ollama --tail=50
```

and:

``` powershell
docker compose logs backend --tail=50
```

------------------------------------------------------------------------

# 8. Application URL

**http://localhost:3001**

------------------------------------------------------------------------

# 9. Ollama

Ollama is already included in the Docker setup.

You do **not** need to install Ollama separately on Windows.

**Official Ollama website:**\
https://ollama.com/

**Official Ollama Docker image:**\
https://hub.docker.com/r/ollama/ollama

------------------------------------------------------------------------

# 10. Technology Stack

-   React
-   Vite
-   FastAPI
-   PostgreSQL
-   Nginx
-   Docker
-   Ollama
-   Llama 3.2 3B

------------------------------------------------------------------------

# Quick Setup

For a fresh computer:

``` powershell
docker compose down
```

``` powershell
$env:OLLAMA_ENABLED="true"
```

``` powershell
docker compose --profile ai up -d --build
```

**First time only:**

``` powershell
docker compose exec ollama ollama pull llama3.2:3b
```

Then:

``` powershell
docker compose restart backend
```

Check:

``` powershell
docker compose ps
```

Open:

**http://localhost:3001**

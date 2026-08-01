# Git Workflow Guide

## Overview
This document covers the complete Git workflow for the AI Stock Scanner project, including uploading, downloading, versioning, and best practices.

---

## 1. Initial Setup (One-Time)

### Clone the Repository (Download)
```cmd
cd /d D:\SCTS
git clone https://github.com/arkcdeff/AIStockScanner.git
cd AIStockScanner
```

### Configure Git Identity (One-Time)
```cmd
git config user.name "Your Name"
git config user.email "your.email@example.com"
```

---

## 2. Daily Workflow

### Check Current Status
```cmd
git status
```

### See What Changed
```cmd
git diff              # Unstaged changes
git diff --staged     # Staged changes (ready to commit)
```

### View Commit History
```cmd
git log --oneline --graph --all   # Compact view with branch graph
git log                            # Full commit history
```

---

## 3. Making Changes & Uploading (Push)

### Step-by-Step Push Process

```cmd
# 1. Check what files changed
git status

# 2. Stage specific files (recommended)
git add app/main.py
git add app/api/          # Add entire folder

# OR stage all changes
git add -A

# 3. Commit with a meaningful message
git commit -m "feat: add stock price endpoint"

# 4. Pull latest changes from remote (avoid conflicts)
git pull origin main

# 5. Push to GitHub
git push origin main
```

### Commit Message Convention
| Prefix | When to Use |
|--------|-------------|
| `feat:` | New feature |
| `fix:` | Bug fix |
| `docs:` | Documentation changes |
| `refactor:` | Code restructuring |
| `test:` | Adding/updating tests |
| `chore:` | Maintenance tasks |

**Examples:**
- `feat: add stock price prediction endpoint`
- `fix: correct database connection timeout`
- `docs: update API documentation`
- `refactor: optimize scanner registry`

---

## 4. Downloading Updates (Pull)

### Get Latest Code
```cmd
# Simple pull
git pull origin main

# If you have local changes, stash them first
git stash
git pull origin main
git stash pop
```

### Reset to Remote Version (Discard Local Changes)
```cmd
git fetch origin
git reset --hard origin/main
```

---

## 5. Versioning (Creating Releases)

### Create a Version Tag
```cmd
# List existing tags
git tag

# Create a lightweight tag
git tag v1.0.0

# Create an annotated tag (recommended - includes message)
git tag -a v1.0.0 -m "Milestone 1: FastAPI foundation complete"

# Push tag to GitHub
git push origin v1.0.0

# Push all tags
git push origin --tags
```

### Version Numbering (Semantic Versioning)
```
v1.0.0
^ ^ ^
| | └── Patch (bug fixes)
| └──── Minor (new features, backward compatible)
└────── Major (breaking changes)
```

**Examples:**
- `v1.0.0` - First stable release
- `v1.1.0` - Added new feature
- `v1.1.1` - Bug fix
- `v2.0.0` - Breaking API changes

### Delete a Tag
```cmd
git tag -d v1.0.0                    # Delete locally
git push origin --delete v1.0.0      # Delete on GitHub
```

---

## 6. Branching Strategy

### Create a Feature Branch
```cmd
# Create and switch to new branch
git checkout -b feature/add-auth

# Work on your changes, then commit
git add -A
git commit -m "feat: add authentication"

# Push branch to GitHub
git push -u origin feature/add-auth

# Create a Pull Request on GitHub, then merge
```

### Switch Between Branches
```cmd
git checkout main                    # Switch to main
git checkout feature/add-auth        # Switch to feature branch
```

### Merge a Branch into Main
```cmd
git checkout main
git pull origin main
git merge feature/add-auth
git push origin main
```

### Delete a Branch (After Merge)
```cmd
git branch -d feature/add-auth       # Delete locally
git push origin --delete feature/add-auth  # Delete on GitHub
```

---

## 7. Undoing Changes

### Undo Last Commit (Keep Changes)
```cmd
git reset --soft HEAD~1
```

### Undo Last Commit (Discard Changes)
```cmd
git reset --hard HEAD~1
```

### Unstage a File
```cmd
git reset HEAD filename.py
```

### Discard Changes in a File
```cmd
git checkout -- filename.py
```

---

## 8. Handling Conflicts

When `git pull` shows a conflict:

1. Open the conflicting file(s) - marked with `<<<<<<<`, `=======`, `>>>>>>>`
2. Choose which changes to keep
3. Remove the conflict markers
4. Save the file
5. Stage and commit:
```cmd
git add filename.py
git commit -m "fix: resolve merge conflict"
```

---

## 9. Complete Project Setup After Clone

After cloning the repository, run these commands to set up the project:

```cmd
cd D:\SCTS\AIStockScanner

# Create virtual environment
py -3.13 -m venv .venv

# Activate it
.venv\Scripts\activate

# Install dependencies
python -m pip install --upgrade pip uv
uv sync --extra dev --link-mode=copy

# Configure environment
copy .env.example .env
# Edit .env - replace YOUR_PASSWORD with your PostgreSQL password

# Run migrations
uv run alembic upgrade head

# Start the app
uv run uvicorn app.main:app --reload
```

---

## 10. Quick Reference

| Command | Description |
|---------|-------------|
| `git status` | Check file status |
| `git add -A` | Stage all changes |
| `git commit -m "message"` | Commit staged changes |
| `git push origin main` | Upload to GitHub |
| `git pull origin main` | Download from GitHub |
| `git tag -a v1.0 -m "msg"` | Create version tag |
| `git push origin --tags` | Upload all tags |
| `git log --oneline` | View commit history |
| `git checkout -b branch-name` | Create new branch |
| `git branch` | List branches |
| `git stash` | Temporarily save changes |
| `git stash pop` | Restore stashed changes |

---

## 11. GitHub Release Process

1. **Tag the version:**
   ```cmd
   git tag -a v1.0.0 -m "Milestone 1: FastAPI foundation complete"
   git push origin v1.0.0
   ```

2. **Create a Release on GitHub:**
   - Go to https://github.com/arkcdeff/AIStockScanner/releases
   - Click "Create a new release"
   - Select the tag (e.g., `v1.0.0`)
   - Add release notes describing what changed
   - Publish release

3. **Download a specific version:**
   ```cmd
   git clone --branch v1.0.0 https://github.com/arkcdeff/AIStockScanner.git
   # OR switch to existing repo
   git checkout v1.0.0
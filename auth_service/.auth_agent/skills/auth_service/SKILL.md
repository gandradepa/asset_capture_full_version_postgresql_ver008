---
name: auth_service
description: Developer skill guide for managing the shared Auth Service. Covers integrating authentication into Flask apps, modifying shared SQLAlchemy models, and managing user accounts via the CLI.
---

# Auth Service Skill

Current documentation refresh: 2026-04-28.

## Use this skill when
- Integrating authentication and session management into a new Flask application.
- Modifying the shared `User` or `FLSAsset` SQLAlchemy models.
- Resetting passwords or creating new user accounts for reviewers and technicians.
- Debugging `flask_login` session issues across the platform.

## Do not use this skill when
- Modifying the aesthetic or HTML of the `/login` page itself (that is managed in each host application's `.agent` documentation).
- Modifying asset extraction algorithms (refer to `API/.agent`).

## Instructions
Before editing `auth_model.py`, consider the ripple effect: any schema change must be migrated or reflected across all apps currently consuming that model (Dashboard, Capture App, Review Apps). 

## 1. Integrating Auth into a New Flask App

To embed authentication into a new app, follow this pattern:

```python
import sys
import os
from flask import Flask

# 1. Add auth_service to path
AUTH_SERVICE_PATH = os.getenv("AUTH_SERVICE_PATH", r"C:\path\to\auth_service")
if AUTH_SERVICE_PATH not in sys.path:
    sys.path.append(AUTH_SERVICE_PATH)

# 2. Import extensions
from auth_model import db, bcrypt
from auth_controller import login_manager

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'default-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URI', f"sqlite:///{AUTH_SERVICE_PATH}/users.db")

# 3. Initialize extensions within app context
db.init_app(app)
bcrypt.init_app(app)
login_manager.init_app(app)
```

## 2. Shared Sub-Models
The `auth_model.py` file also defines non-user tables, primarily for the Dashboard's FLS system:
- `FLSAsset`: A mirror model of `new_device`.
- `Property`, `Space`, `AssetGroup`, `DeviceTypeMap`: Normalization tables for dropdowns and mappings.

If adding a new column to `FLSAsset`, ensure you also update its `to_dict()` serialization method.

## 3. Creating CLI Admin Tools
CLI tools (`init_db.py`, `reset_password.py`) must mimic the Flask application context using a dummy app:

```python
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////home/developer/auth_service/users.db'
db.init_app(app)

with app.app_context():
    # Database logic goes here
    user = User.query.get(1)
```

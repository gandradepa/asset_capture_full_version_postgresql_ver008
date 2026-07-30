# Auth Service Agent

Current documentation refresh: 2026-04-28.
## Purpose
Provide shared access management for all web application entry points.
## Scope
**In-scope**: Session management via Flask-Login, bcrypt hashing.
**Out-of-scope**: Role-based access control inside modules (currently purely authed/unauthed).
## Inputs
User/Password endpoints.
## Outputs
Signed Flask session cookies.
## Dependencies
`User_control.db`
## Key Paths & Env Vars
- `auth_service.env` (Must configure secure cookies).
## Critical Conventions
The module is consumed as a library blueprint by Dashboard, SDI, Review, and Capture. Do not break blueprint compatibilities.
## Validation Checklist
- [ ] Secure session secrets.

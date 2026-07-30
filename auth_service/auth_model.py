# /home/developer/auth_service/auth_model.py

from functools import wraps
from flask import jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, current_user
from flask_bcrypt import Bcrypt
from sqlalchemy import inspect, text

# Initialize the extensions, but don't tie them to an app yet.
# This allows us to use them in different apps.
db = SQLAlchemy()
bcrypt = Bcrypt()

def ensure_user_name_column():
    """Add the nullable user display-name column to existing auth databases."""
    table_name = User.__table__.name
    inspector = inspect(db.engine)
    if not inspector.has_table(table_name):
        return False
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "name" in columns:
        return False
    quoted_table = table_name.replace('"', '""')
    with db.engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{quoted_table}" ADD COLUMN name VARCHAR(120)'))
    return True


def ensure_user_active_column():
    """Add active column (NOT NULL DEFAULT 1) to existing auth databases."""
    table_name = User.__table__.name
    inspector = inspect(db.engine)
    if not inspector.has_table(table_name):
        return False
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "active" in columns:
        return False
    quoted_table = table_name.replace('"', '""')
    with db.engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{quoted_table}" ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1'))
    return True


def ensure_user_is_admin_column():
    """Add is_admin column (NOT NULL DEFAULT 0) to existing auth databases."""
    table_name = User.__table__.name
    inspector = inspect(db.engine)
    if not inspector.has_table(table_name):
        return False
    columns = {column["name"] for column in inspector.get_columns(table_name)}
    if "is_admin" in columns:
        return False
    quoted_table = table_name.replace('"', '""')
    with db.engine.begin() as conn:
        conn.execute(text(f'ALTER TABLE "{quoted_table}" ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0'))
    return True


class User(db.Model, UserMixin):
    """Defines the User database model."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)

    def set_password(self, password):
        """Hashes and sets the user's password."""
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        """Checks if a provided password matches the hash."""
        return bcrypt.check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'


class UserAccess(db.Model):
    """
    Stores per-user permission grants for application sections and items.
    Absence of a row means no access.
    access_level: 'admin' | 'editor' | 'viewer'
    item_key='': reserved for section-level grants.
    """
    __tablename__ = "user_access"

    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(
                       db.Integer,
                       db.ForeignKey("user.id", ondelete="CASCADE"),
                       nullable=False,
                       index=True,
                   )
    section_key  = db.Column(db.String(80), nullable=False)
    item_key     = db.Column(db.String(80), nullable=False, default="")
    access_level = db.Column(db.String(20), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "section_key", "item_key",
                            name="uq_user_access_grant"),
    )

    user = db.relationship("User", backref=db.backref("access_grants", lazy="dynamic"))

    def __repr__(self):
        return (f"<UserAccess user={self.user_id} "
                f"{self.section_key}/{self.item_key} => {self.access_level}>")


def ensure_user_access_table():
    """Create the user_access table if it does not exist. Idempotent."""
    inspector = inspect(db.engine)
    if inspector.has_table("user_access"):
        return False
    with db.engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE user_access (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL
                                 REFERENCES "user" (id) ON DELETE CASCADE,
                section_key  VARCHAR(80) NOT NULL,
                item_key     VARCHAR(80) NOT NULL DEFAULT '',
                access_level VARCHAR(20) NOT NULL,
                UNIQUE (user_id, section_key, item_key)
            )
        """))
        conn.execute(text(
            "CREATE INDEX ix_user_access_user_id ON user_access (user_id)"
        ))
    return True


_ACCESS_RANK = {"admin": 3, "editor": 2, "viewer": 1}


def has_permission(user, section_key: str, item_key: str, required_level: str) -> bool:
    """
    Return True if user holds at least required_level on section_key/item_key.

    Resolution:
      Only an exact section/item grant applies. Admin is the highest role for
      that item, not a wildcard grant for unrelated applications.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False

    required_rank = _ACCESS_RANK.get(required_level, 0)
    grants = UserAccess.query.filter_by(user_id=user.id).all()

    best_rank = 0
    for g in grants:
        if g.section_key == section_key and g.item_key == item_key:
            best_rank = max(best_rank, _ACCESS_RANK.get(g.access_level, 0))

    return best_rank >= required_rank


def is_admin(user) -> bool:
    """Return True if user has the is_admin flag set, or any legacy admin-level grant."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_admin", False):
        return True
    return (
        UserAccess.query.filter_by(user_id=user.id, access_level="admin").first()
        is not None
    )


def admin_required(fn):
    """Route decorator: restricts to users with any admin grant. Use after @login_required."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"success": False, "message": "Authentication required."}), 401
        if not is_admin(current_user):
            return jsonify({"success": False, "message": "Admin access required."}), 403
        return fn(*args, **kwargs)
    return wrapper


def require_permission(section_key: str, item_key: str, level: str):
    """
    Decorator factory: checks current_user holds at least `level` on section_key/item_key.
    Returns 403 JSON on failure. Use after @login_required.
    Use for API/mutation routes. For HTML page routes use access_denied_response() inline.
    """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_permission(current_user, section_key, item_key, level):
                return jsonify({
                    "success": False,
                    "message": "You do not have permission to perform this action.",
                }), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def access_denied_response(app_label: str, logout_url: str = "/logout") -> tuple:
    """
    Return an HTML 403 Access Denied page for browser-rendered routes.
    Use instead of @require_permission on GET routes that render HTML.
    logout_url defaults to /logout (no blueprint prefix). Pass /auth/logout
    for apps that register auth_bp with url_prefix='/auth' (e.g. asset_capture_app_dev).
    """
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Access Denied</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ font-family: sans-serif; text-align: center; padding: 2rem; background: #f8f9fa; color: #333; }}
        h2 {{ color: #dc3545; }}
        a.btn {{ display: inline-block; margin-top: 1rem; padding: 0.5rem 1rem; background-color: #0b2265; color: white; text-decoration: none; border-radius: 4px; }}
    </style>
</head>
<body>
    <h2>Access Denied</h2>
    <p>You do not have permission to access the {app_label}.</p>
    <p>Please contact your administrator if you need access.</p>
    <a href="{logout_url}" class="btn">Log Out</a>
</body>
</html>"""
    return html, 403


class FLSAsset(db.Model):
    __tablename__ = 'fls_assets'
    index = db.Column(db.String(50), primary_key=True)
    work_order = db.Column(db.String(100))
    asset_tag = db.Column(db.String(100))
    asset_group = db.Column(db.String(255))
    description = db.Column(db.String(500))
    property = db.Column(db.String(255))
    space = db.Column(db.String(255))
    attribute_set = db.Column(db.String(255))
    device_address = db.Column(db.String(100))
    device_type = db.Column(db.String(255))
    un_account_number = db.Column(db.String(100))
    planon_code = db.Column(db.String(100))
    creation_date = db.Column(db.String(20))
    status = db.Column(db.String(20))
    workflow = db.Column(db.String(100))

    def to_dict(self):
        """Serializes the object to a dictionary."""
        return {
            'index': self.index,
            'work_order': self.work_order,
            'asset_tag': self.asset_tag,
            'asset_group': self.asset_group,
            'description': self.description,
            'property': self.property,
            'space': self.space,
            'attribute_set': self.attribute_set,
            'device_address': self.device_address,
            'device_type': self.device_type,
            'un_account_number': self.un_account_number,
            'planon_code': self.planon_code,
            'creation_date': self.creation_date,
            'status': self.status,
            'workflow': self.workflow
        }

class Property(db.Model):
    __tablename__ = 'properties'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False, index=True)
    spaces = db.relationship('Space', backref='property', lazy=True, cascade="all, delete-orphan")

class Space(db.Model):
    __tablename__ = 'spaces'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    property_id = db.Column(db.Integer, db.ForeignKey('properties.id'), nullable=False)

class AssetGroup(db.Model):
    __tablename__ = 'asset_groups'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)

class DeviceTypeMap(db.Model):
    __tablename__ = 'device_type_map'
    id = db.Column(db.Integer, primary_key=True)
    classification_key = db.Column(db.String(255), unique=True, nullable=False, index=True)
    device_type = db.Column(db.String(255), nullable=False)

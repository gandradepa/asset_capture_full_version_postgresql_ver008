# /home/developer/auth_service/init_db.py

import sys
import argparse  # NOVO: Importa a biblioteca para argumentos de linha de comando
from flask import Flask
from getpass import getpass # NOVO: Para inserir a senha de forma segura

# Add the auth_service directory to the path to find our models
sys.path.append('/home/developer/auth_service')
from auth_model import db, User, ensure_user_name_column

# --- NOVO: Configuração dos argumentos ---
# Isso define como o script vai interpretar os comandos que você passar no terminal
parser = argparse.ArgumentParser(description="Manage users in the database.")
parser.add_argument('username', help="The username for the new user.")
parser.add_argument('email', help="The email address for the new user.")
parser.add_argument('--password', '-p', help="The password for the user (will be prompted if not provided).")

# Processa os argumentos fornecidos ao rodar o script
args = parser.parse_args()
# ----------------------------------------


# We need to create a temporary Flask app to have the "context"
# required to interact with the database.
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////home/developer/auth_service/users.db'
db.init_app(app)

with app.app_context():
    print("Creating database tables if they don't exist...")
    db.create_all()
    ensure_user_name_column()
    print("Tables checked/created.")

    # --- LÓGICA ATUALIZADA PARA ADICIONAR UM ÚNICO USUÁRIO ---

    # Pega o nome de usuário e email dos argumentos
    username_to_add = args.username
    email_to_add = args.email

    # Verifica se o usuário já existe
    if not User.query.filter_by(username=username_to_add).first():
        print(f"Creating new user '{username_to_add}'...")
        
        # Pega a senha do argumento ou pede para o usuário digitar
        password = args.password
        if not password:
            password = getpass(f"Enter password for {username_to_add}: ")
        
        new_user = User(
            username=username_to_add,
            email=email_to_add
        )
        new_user.set_password(password) # Usa a senha fornecida
        db.session.add(new_user)
        db.session.commit()
        print(f"User '{username_to_add}' created successfully.")
    else:
        print(f"User '{username_to_add}' already exists, skipping creation.")

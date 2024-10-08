from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from flask import Flask, flash, request, redirect, Response, render_template, session
from werkzeug.utils import secure_filename
from sqlalchemy import URL
import mimetypes
from Cryptodome.Cipher import ChaCha20
from Cryptodome.Random import get_random_bytes
from Cryptodome.Protocol.KDF import PBKDF2
from Cryptodome.Hash import SHA512
from argon2 import PasswordHasher
import random
import string
import os
import struct

import model

from dotenv import load_dotenv
load_dotenv()

CONTAINER_NAME = 'userfiles'
SQL_COPT_SS_ACCESS_TOKEN = 1256  # Connection option for access tokens, as defined in msodbcsql.h
TOKEN_URL = "https://database.windows.net/"  # The token URL for any Azure SQL database

default_credential = DefaultAzureCredential()

account_url = "https://trashcancy.blob.core.windows.net"
blob_service_client = BlobServiceClient(account_url, credential=default_credential)

app = Flask(__name__)
# get app.config values from environment variable
app.config.from_prefixed_env()
# set maximum upload size to 25mb
app.config['MAX_CONTENT_LENGTH'] = 25 * 1000 * 1000

connection_string = os.getenv('AZURE_SQL_CONNECTIONSTRING')
connection_url = URL.create("mssql+pyodbc", query={"odbc_connect": connection_string})
token_bytes = default_credential.get_token(TOKEN_URL).token.encode('utf-16-le')
token_struct = struct.pack(f'<I{len(token_bytes)}s', len(token_bytes), token_bytes)
app.config['SQLALCHEMY_DATABASE_URI'] = connection_url
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True, "connect_args": {"attrs_before": {SQL_COPT_SS_ACCESS_TOKEN: token_struct}, "timeout": 300}}
model.db.init_app(app)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash("No file part")
        return redirect('/')
    file = request.files['file']
    if file.filename == '':
        flash('No selected file')
        return redirect('/')
    filename = secure_filename(file.filename)
    password = request.form['password']
    encrypt = password != ''

    file_length = file.seek(0, os.SEEK_END)
    file.seek(0, os.SEEK_SET)
    
    uri = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=uri)
    if encrypt:
        def encrypt_file(cipher):
            while (buf := file.read(4096)):
                yield cipher.encrypt(buf)

        salt = get_random_bytes(16)
        key = PBKDF2(password, salt, 32, hmac_hash_module=SHA512)
        cipher = ChaCha20.new(key=key)
        nonce = cipher.nonce
        password_hash = PasswordHasher().hash(password)
        blob_client.upload_blob(data=encrypt_file(cipher), length=file_length)
        model.new_file(encrypt, filename, uri, password_hash, salt, nonce)
    else:
        blob_client.upload_blob(data=file, length=file_length)
        model.new_file(encrypt, filename, uri)
    return render_template('file.html', uri=uri, filename=filename)

@app.route('/dl/<uri>', methods=['GET', 'POST'])
def download_file(uri):
    blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=uri)
    if request.method == 'GET':
        # may be deleted by lifecycle policy and stil exist in database so check here
        if not blob_client.exists():
            return 'file does not exist', 404
        file = model.get_file(uri)
        if file.encrypted:
            session['file_id'] = file.id
            return render_template('encrypted.html', uri=uri, filename=file.filename)
    else:
        file = model.db.get_or_404(model.Userfiles, session.get('file_id'))
        password = request.form['password']
        try:
            ph = PasswordHasher()
            ph.verify(file.password_hash, password)
        except:
            return 'unauthorized', 401
        key = PBKDF2(password, file.salt, 32, hmac_hash_module=SHA512)
        cipher = ChaCha20.new(key=key, nonce=file.nonce)

    stream = blob_client.download_blob()
    def generate_file():
        while (chunk := stream.read(4096)):
            if file.encrypted:
                yield cipher.decrypt(chunk)
            else:
                yield chunk
    
    return Response(generate_file(), mimetype=mimetypes.guess_type(file.filename)[0], headers={"Content-Disposition": f"inline; filename={file.filename}"})

@app.route('/')
def index():
    return render_template('index.html')
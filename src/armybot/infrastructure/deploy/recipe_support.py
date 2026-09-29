def common_git_prep(
    project: str,
    repo_url: str,
    branch: str,
    base_path: str,
    app_path: str,
) -> str:
    return f"""
PROJECT='{project}'
REPO_URL='{repo_url}'
BRANCH='{branch}'
BASE_PATH='{base_path}'
APP_PATH='{app_path}'
PROJECT_ROOT="$BASE_PATH/.army-projects/$PROJECT"
RELEASES_DIR="$PROJECT_ROOT/releases"
SHARED_DIR="$PROJECT_ROOT/shared"
RELEASE_ID="$(date -u +%Y%m%d%H%M%S)-$$"
APP_DIR="$RELEASES_DIR/$RELEASE_ID"
BUILD_DIR="$APP_DIR/$APP_PATH"

echo "[1/7] Preparing isolated release"
sudo mkdir -p "$RELEASES_DIR" "$SHARED_DIR"
sudo chown -R "$USER":"$USER" "$PROJECT_ROOT"

echo "[2/7] Cloning repository into $RELEASE_ID"
if ! git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"; then
  rm -rf "$APP_DIR"
  echo "Branch $BRANCH not found, cloning default branch..."
  git clone --depth 1 "$REPO_URL" "$APP_DIR"
fi

if [ ! -d "$BUILD_DIR" ]; then
  echo "Configured app path does not exist: $APP_PATH" >&2
  rm -rf "$APP_DIR"
  exit 19
fi

# Keep environment and runtime state outside immutable releases.
if [ ! -f "$SHARED_DIR/.env" ]; then
  if [ -f "$APP_DIR/.env.example" ]; then
    cp "$APP_DIR/.env.example" "$SHARED_DIR/.env"
  elif [ -f "$BUILD_DIR/.env.example" ]; then
    cp "$BUILD_DIR/.env.example" "$SHARED_DIR/.env"
  else
    touch "$SHARED_DIR/.env"
  fi
fi
ln -sfn "$SHARED_DIR/.env" "$APP_DIR/.env"
if [ "$BUILD_DIR" != "$APP_DIR" ] && [ ! -e "$BUILD_DIR/.env" ]; then
  ln -s "$SHARED_DIR/.env" "$BUILD_DIR/.env"
fi

finalize_release() {{
  ln -sfn "$APP_DIR" "$PROJECT_ROOT/current"
  find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr | tail -n +2 | cut -d' ' -f2- | xargs -r rm -rf
}}


# Auto-provision PostgreSQL database if project uses Postgres
DB_ROLE=$(echo "{project}" | tr '-' '_' | tr '.' '_')
if grep -rqi "postgresql\\|npgsql\\|psycopg\\|postgres:\\|pg_hba" "$APP_DIR" --exclude-dir=".git" --exclude-dir="node_modules" --exclude-dir=".venv" 2>/dev/null; then
  sudo -u postgres psql -c "DO \\$\\$ BEGIN IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$DB_ROLE') THEN CREATE ROLE $DB_ROLE WITH LOGIN PASSWORD 'pass_$DB_ROLE'; END IF; END \\$\\$;" 2>/dev/null || true
  sudo -u postgres psql -c "SELECT 1 FROM pg_database WHERE datname = '$DB_ROLE'" 2>/dev/null | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE $DB_ROLE OWNER $DB_ROLE;" 2>/dev/null || true
  sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE $DB_ROLE TO $DB_ROLE;" 2>/dev/null || true
  if ! grep -q "^DATABASE_URL=" "$APP_DIR/.env" 2>/dev/null; then
    echo "DATABASE_URL=postgresql://$DB_ROLE:pass_$DB_ROLE@localhost:5432/$DB_ROLE" >> "$APP_DIR/.env"
  fi
fi

# Auto-inject Redis URL if redis is referenced
if grep -rqi "redis" "$APP_DIR" --exclude-dir=".git" --exclude-dir="node_modules" --exclude-dir=".venv" 2>/dev/null; then
  if ! grep -q "^REDIS_URL=" "$APP_DIR/.env" 2>/dev/null; then
    echo "REDIS_URL=localhost:6379" >> "$APP_DIR/.env"
  fi
fi

# Auto-inject MongoDB URI if mongodb is referenced
if grep -rqi "mongodb\\|mongoose" "$APP_DIR" --exclude-dir=".git" --exclude-dir="node_modules" 2>/dev/null; then
  sudo systemctl is-active mongod >/dev/null 2>&1 || sudo systemctl start mongod 2>/dev/null || true
  if ! grep -q "^MONGO_URI=" "$APP_DIR/.env" 2>/dev/null; then
    echo "MONGO_URI=mongodb://127.0.0.1:27017/$DB_ROLE" >> "$APP_DIR/.env"
  fi
  if ! grep -q "^MONGODB_URI=" "$APP_DIR/.env" 2>/dev/null; then
    echo "MONGODB_URI=mongodb://127.0.0.1:27017/$DB_ROLE" >> "$APP_DIR/.env"
  fi
fi

# Auto-inject JWT_SECRET if referenced or empty
if grep -rqi "JWT_SECRET" "$APP_DIR" --exclude-dir=".git" --exclude-dir="node_modules" 2>/dev/null; then
  if ! grep -q "^JWT_SECRET=[a-zA-Z0-9]" "$APP_DIR/.env" 2>/dev/null; then
    sed -i '/^JWT_SECRET=/d' "$APP_DIR/.env" 2>/dev/null || true
    echo "JWT_SECRET=$(openssl rand -hex 32)" >> "$APP_DIR/.env"
  fi
fi

for subenv in "$APP_DIR/server" "$APP_DIR/backend" "$APP_DIR/api"; do
  if [ -d "$subenv" ] && [ -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env" "$subenv/.env" 2>/dev/null || true
  fi
done
"""

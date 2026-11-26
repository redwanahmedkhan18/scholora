#!/usr/bin/env bash
# Print the current 6-digit TOTP for a seeded staff account.
#
# ADMIN and SENIOR_COUNSELOR are in MFA_REQUIRED_ROLES, so logging in as either needs a
# second factor. The development seed enrols them with fixed secrets (see
# backend/app/seed_data.py) precisely so this script can hand you a working code without an
# authenticator app. Codes rotate every 30 seconds.
#
#   scripts/mfa-code.sh                       # default: admin@scholara.com.bd
#   scripts/mfa-code.sh senior@scholara.com.bd
#
# DEVELOPMENT ONLY. The seeded secrets are published in this repository and are therefore
# public, so this script refuses to run against a production configuration: a production
# administrator enrols through the app (Admin -> My security) and, if the device is lost,
# through the recovery-code flow or `python -m app.mgmt_admin --reset-mfa`.
set -euo pipefail

if [ "${ENVIRONMENT:-development}" = "production" ]; then
  echo "Refusing to print MFA codes for a production environment." >&2
  echo "Use the in-app enrolment/recovery flow, or: python -m app.mgmt_admin --reset-mfa <email>" >&2
  exit 1
fi

EMAIL="${1:-admin@scholara.com.bd}"
cd "$(dirname "$0")/../backend"

: "${DATABASE_URL:=postgresql+psycopg://scholara:scholara@127.0.0.1:5432/scholara}"
export DATABASE_URL

python3 - "$EMAIL" <<'PY'
import sys
sys.path.insert(0, ".")
from app.core.database import SessionLocal
from app.core import mfa
from app.models.crm import User

email = sys.argv[1].lower().strip()
db = SessionLocal()
try:
    user = db.query(User).filter(User.email == email).first()
    if not user:
        sys.exit(f"No such user: {email}")
    if not user.mfa_secret:
        print(f"{email} has no MFA secret - it logs in with a password only.")
    elif not user.mfa_confirmed_at:
        print(f"{email} started enrolment but never confirmed it.")
    else:
        print(f"{email}  ->  {mfa.current_code(user.mfa_secret)}   (valid for the rest of this 30s window)")
finally:
    db.close()
PY

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
# Real deployments must re-enrol through POST /admin/auth/mfa/enroll - the seeded secrets are
# published in this repository and are therefore public.
set -euo pipefail

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

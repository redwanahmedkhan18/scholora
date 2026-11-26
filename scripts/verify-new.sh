#!/usr/bin/env bash
# Exercise every endpoint added in the spec-gap build. Any non-expected status fails loudly.
set -uo pipefail
API=${API:-http://localhost:8000/api/v1}
ORIGIN=${ORIGIN:-${API%/api/v1}}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY="$ROOT/.venv/bin/python"; [ -x "$PY" ] || PY=python3
PASS=0; FAIL=0

chk() { # chk <label> <expected> <actual>
  if [ "$2" = "$3" ]; then printf '  \033[32mPASS\033[0m %-52s %s\n' "$1" "$3"; PASS=$((PASS+1));
  else printf '  \033[31mFAIL\033[0m %-52s got %s want %s\n' "$1" "$3" "$2"; FAIL=$((FAIL+1)); fi
}
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# This script deliberately exhausts the login bucket (10 attempts / 5 min) to prove the
# limiter works, so a second run inside that window cannot authenticate and every
# authenticated assertion would fail for the wrong reason. Detect that and say so.
PREFLIGHT=$(curl -s -X POST "$API/admin/auth/login" -H 'content-type: application/json' \
  -d '{"email":"admin@scholara.com.bd","password":"Admin@12345"}')
if echo "$PREFLIGHT" | grep -q retry_after_seconds; then
  WAIT=$(echo "$PREFLIGHT" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("retry_after_seconds","?"))')
  echo
  echo "  The login rate limit is still cooling down from a previous run."
  echo "  This script exhausts it on purpose to verify throttling works."
  echo "  Wait ${WAIT}s and run it again."
  echo "  Do not solve this by starting the API with RATE_LIMIT_ENABLED=false: with the limiter"
  echo "  off, the two rate-limiting checks below cannot pass and would report a false failure."
  echo
  exit 2
fi

echo "── auth ───────────────────────────────────────────────────────────"
# ADMIN is in MFA_REQUIRED_ROLES, so the password step withholds the token and the seeded
# development secret has to answer the challenge. A password-only login handing back a
# working session is exactly the regression this checks for.
chk "admin password step withholds the token" true \
  "$(echo "$PREFLIGHT" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(str(bool(d.get("mfa_required")) and not d.get("access_token")).lower())')"
# Complete the sign-in properly: the password step returns a challenge token, never a session,
# and an account whose published secret was revoked (migration a7b8c9d0e1f2) has to enrol first.
# Both paths are exercised by the same helper so the rest of the script always has a session.
TOKEN=$("$PY" - "$API" "$ROOT" <<'PYEOF'
import json, sys, urllib.error, urllib.request
api, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, root + "/backend")
from app.core import mfa
from app import seed_data as D

def post(path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api + path, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")

_, body = post("/admin/auth/login", {"email": "admin@scholara.com.bd", "password": "Admin@12345"})
if body.get("mfa_enrolment_required"):
    _, enr = post("/admin/auth/mfa/enroll", {}, body["access_token"])
    _, conf = post("/admin/auth/mfa/confirm", {"code": mfa.current_code(enr["secret"])},
                   body["access_token"])
    print(conf.get("access_token", ""))
elif body.get("mfa_required"):
    _, done = post("/admin/auth/mfa/challenge",
                   {"challenge_token": body["challenge_token"],
                    "code": mfa.current_code(D.DEV_MFA_SECRET_ADMIN)})
    print(done.get("access_token", ""))
else:
    print(body.get("access_token", ""))
PYEOF
)
if [ -z "$TOKEN" ]; then
  echo "  the seeded administrator's TOTP secret is not the published development one."
  echo "  Either re-seed (python -m app.seed), reset it (python -m app.mgmt_admin --reset-mfa admin@scholara.com.bd)"
  echo "  and re-enrol, or point this script at a freshly seeded database."
fi
[ -n "$TOKEN" ] && chk "admin login (password + TOTP)" 1 1 || chk "admin login (password + TOTP)" 1 0
AUTH="authorization: Bearer $TOKEN"

echo "── mandatory MFA is enforced, not merely advised ──────────────────"
# A SENIOR_COUNSELOR (mandatory-MFA role) created without a secret must not receive a
# usable session - only a token scoped to enrolment.
WALL_EMAIL="mfa-wall-$$@scholara.com.bd"
WALL_UID=$(curl -s -X POST "$API/admin/users" -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"email\":\"$WALL_EMAIL\",\"full_name\":\"Wall Test\",\"role\":\"SENIOR_COUNSELOR\",\"password\":\"Testing@12345\",\"is_active\":true}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')
WALL=$(curl -s -X POST "$API/admin/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$WALL_EMAIL\",\"password\":\"Testing@12345\"}")
chk "unenrolled privileged login is scoped to enrolment" true \
  "$(echo "$WALL" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(str(d.get("mfa_enrolment_required") is True and d.get("scope")=="mfa_enrolment").lower())')"
WTOK=$(echo "$WALL" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))')
chk "scoped token is refused by the dashboard"  403 "$(code "$API/admin/dashboard" -H "authorization: Bearer $WTOK")"
chk "scoped token is refused by /auth/me"       403 "$(code "$API/admin/auth/me"   -H "authorization: Bearer $WTOK")"
chk "scoped token may still reach enrolment"    200 "$(code -X POST "$API/admin/auth/mfa/enroll" -H "authorization: Bearer $WTOK")"
chk "DELETE wall-test user"                     200 "$(code -X DELETE "$API/admin/users/$WALL_UID" -H "$AUTH")"

echo "── staff MFA (§89) ────────────────────────────────────────────────"
# Uses a throwaway account so the demo logins stay password-only.
MFA_EMAIL="mfa-test-$$@scholara.com.bd"
TEST_UID=$(curl -s -X POST "$API/admin/users" -H "$AUTH" -H 'content-type: application/json' \
  -d "{\"email\":\"$MFA_EMAIL\",\"full_name\":\"MFA Test\",\"role\":\"COUNSELOR\",\"password\":\"Testing@12345\",\"is_active\":true}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("id",""))')
TTOK=$(curl -s -X POST "$API/admin/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$MFA_EMAIL\",\"password\":\"Testing@12345\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin).get("access_token",""))')
TAUTH="authorization: Bearer $TTOK"
chk "GET  /admin/auth/mfa/status"      200 "$(code "$API/admin/auth/mfa/status" -H "$TAUTH")"
SECRET=$(curl -s -X POST "$API/admin/auth/mfa/enroll" -H "$TAUTH" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["secret"])')
totp() { "$PY" -c "import sys; sys.path.insert(0, '$ROOT/backend'); from app.core import mfa; print(mfa.current_code('$SECRET'))"; }
# The enrolment step above consumed the current time-step, and a code is single-use, so the
# login challenge below has to use the *next* one. This is the replay guard working.
totp_next() { "$PY" -c "import sys,time; sys.path.insert(0, '$ROOT/backend'); from app.core import mfa; print(mfa.current_code('$SECRET', time.time() + 30))"; }
chk "POST /admin/auth/mfa/confirm (valid TOTP)" 200 \
  "$(code -X POST "$API/admin/auth/mfa/confirm" -H "$TAUTH" -H 'content-type: application/json' -d "{\"code\":\"$(totp)\"}")"
chk "POST /admin/auth/mfa/confirm (bad code)"   400 \
  "$(code -X POST "$API/admin/auth/mfa/confirm" -H "$TAUTH" -H 'content-type: application/json' -d '{"code":"000000"}')"
MFA_LOGIN=$(curl -s -X POST "$API/admin/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$MFA_EMAIL\",\"password\":\"Testing@12345\"}")
chk "password login withholds token once enrolled" true \
  "$(echo "$MFA_LOGIN" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(str(d.get("mfa_required") and "access_token" not in d).lower())')"
CHALLENGE=$(echo "$MFA_LOGIN" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("challenge_token",""))')
chk "challenge token issued instead of a session" 1 "$([ -n "$CHALLENGE" ] && echo 1 || echo 0)"
chk "POST /admin/auth/mfa/challenge (valid)" 200 \
  "$(code -X POST "$API/admin/auth/mfa/challenge" -H 'content-type: application/json' \
     -d "{\"challenge_token\":\"$CHALLENGE\",\"code\":\"$(totp_next)\"}")"
chk "POST /admin/auth/mfa/challenge (replayed code)" 401 \
  "$(code -X POST "$API/admin/auth/mfa/challenge" -H 'content-type: application/json' \
     -d "{\"challenge_token\":\"$CHALLENGE\",\"code\":\"$(totp_next)\"}")"
chk "POST /admin/auth/mfa/challenge (bad code)" 401 \
  "$(code -X POST "$API/admin/auth/mfa/challenge" -H 'content-type: application/json' \
     -d "{\"challenge_token\":\"$CHALLENGE\",\"code\":\"111111\"}")"
chk "challenge without a ticket is refused" 400 \
  "$(code -X POST "$API/admin/auth/mfa/challenge" -H 'content-type: application/json' \
     -d '{"code":"111111"}')"
chk "challenge with a wrong password is refused" 401 \
  "$(code -X POST "$API/admin/auth/mfa/challenge" -H 'content-type: application/json' \
     -d "{\"email\":\"$MFA_EMAIL\",\"password\":\"not-the-password\",\"code\":\"111111\"}")"
chk "DELETE throwaway user" 200 "$(code -X DELETE "$API/admin/users/$TEST_UID" -H "$AUTH")"
chk "COUNSELOR login needs no second factor" 200 \
  "$(code -X POST "$API/admin/auth/login" -H 'content-type: application/json' \
     -d '{"email":"counselor@scholara.com.bd","password":"Counselor@123"}')"

echo "── student portal (§30, §53-§60) ──────────────────────────────────"
chk "GET  /portal/demo-student"            200 "$(code "$API/portal/demo-student")"
chk "GET  /portal/nope (unknown token)"    404 "$(code "$API/portal/nope")"
chk "POST /portal/../applications" 200 "$(code -X POST "$API/portal/demo-student/applications" \
  -H 'content-type: application/json' -d '{"program_slug":"university-of-texas-at-arlington-msc-artificial-intelligence","intake_label":"Fall 2027"}')"
chk "POST /portal/../shortlist"   200 "$(code -X POST "$API/portal/demo-student/shortlist" \
  -H 'content-type: application/json' -d '{"entity_type":"program","slug":"university-of-texas-at-arlington-phd-computer-science"}')"
chk "POST /portal/../shortlist/sync" 200 "$(code -X POST "$API/portal/demo-student/shortlist/sync" \
  -H 'content-type: application/json' -d '[{"entity_type":"university","slug":"university-of-texas-at-arlington"}]')"
chk "GET  /portal/../shortlist"   200 "$(code "$API/portal/demo-student/shortlist")"

echo "── private documents (§57, §90) ───────────────────────────────────"
printf '%%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%%%EOF\n' > /tmp/transcript.pdf
UP=$(curl -s -X POST "$API/portal/demo-student/documents" -F 'doc_type=BACHELOR_TRANSCRIPT' -F 'file=@/tmp/transcript.pdf;type=application/pdf')
DOC_URL=$(echo "$UP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["document"]["download_url"])' 2>/dev/null)
DOC_ID=$(echo "$UP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["document"]["id"])' 2>/dev/null)
[ -n "$DOC_URL" ] && chk "POST /portal/../documents (PDF upload)" 1 1 || chk "POST /portal/../documents (PDF upload)" 1 0
chk "GET  signed URL"                200 "$(code "$ORIGIN$DOC_URL")"
chk "GET  tampered signature"        403 "$(code "$ORIGIN${DOC_URL%?}X")"
echo 'not-a-pdf' > /tmp/fake.pdf
chk "POST mislabelled .pdf rejected"  400 "$(code -X POST "$API/portal/demo-student/documents" \
  -F 'doc_type=PASSPORT' -F 'file=@/tmp/fake.pdf;type=application/pdf')"
head -c 16000000 /dev/zero > /tmp/big.pdf
chk "POST oversize file rejected"     400 "$(code -X POST "$API/portal/demo-student/documents" \
  -F 'doc_type=PASSPORT' -F 'file=@/tmp/big.pdf;type=application/pdf')"
chk "GET  /admin/documents/queue"     200 "$(code "$API/admin/documents/queue" -H "$AUTH")"
chk "POST /admin/documents/N/review"  200 "$(code -X POST "$API/admin/documents/$DOC_ID/review" -H "$AUTH" \
  -H 'content-type: application/json' -d '{"status":"ACCEPTED","review_note":"Legible and complete."}')"
chk "GET  /admin/documents/N/link"    200 "$(code "$API/admin/documents/$DOC_ID/link?ttl=60" -H "$AUTH")"

echo "── applications pipeline (§53-§56) ────────────────────────────────"
APP_ID=$(curl -s "$API/portal/demo-student" | python3 -c 'import sys,json;a=json.load(sys.stdin)["applications"];print(a[0]["id"] if a else "")')
chk "GET  /admin/applications/board"  200 "$(code "$API/admin/applications/board" -H "$AUTH")"
chk "POST /admin/applications/N/status" 200 "$(code -X POST "$API/admin/applications/$APP_ID/status" -H "$AUTH" \
  -H 'content-type: application/json' -d '{"status":"SUBMITTED","note":"Portal submission confirmed."}')"
chk "POST invalid status rejected"     400 "$(code -X POST "$API/admin/applications/$APP_ID/status" -H "$AUTH" \
  -H 'content-type: application/json' -d '{"status":"MADE_UP"}')"

echo "── counselor + notifications + audit (§59, §60, §91) ──────────────"
chk "GET  /admin/counselor/dashboard"  200 "$(code "$API/admin/counselor/dashboard" -H "$AUTH")"
chk "GET  /admin/notifications/inbox"  200 "$(code "$API/admin/notifications/inbox" -H "$AUTH")"
chk "POST /admin/notifications/read-all" 200 "$(code -X POST "$API/admin/notifications/read-all" -H "$AUTH")"
chk "GET  /admin/audit/log"            200 "$(code "$API/admin/audit/log" -H "$AUTH")"
chk "GET  /admin/audit/log (no auth)"  401 "$(code "$API/admin/audit/log")"

echo "── assistant RAG (§64-§66) ────────────────────────────────────────"
chk "POST /admin/assistant/reindex"    200 "$(code -X POST "$API/admin/assistant/reindex" -H "$AUTH")"
chk "POST /assistant/ask"              200 "$(code -X POST "$API/assistant/ask" -H 'content-type: application/json' \
  -d '{"question":"fully funded PhD supervisors in machine learning"}')"
chk "GET  /admin/assistant/conversations" 200 "$(code "$API/admin/assistant/conversations" -H "$AUTH")"

echo "── jobs (§45, §83) ────────────────────────────────────────────────"
chk "GET  /admin/jobs"                 200 "$(code "$API/admin/jobs" -H "$AUTH")"
for J in verification_sweep deadline_reminders reindex_knowledge campaign_metrics data_quality_digest; do
  chk "POST /admin/jobs/$J/run" 200 "$(code -X POST "$API/admin/jobs/$J/run" -H "$AUTH")"
done

echo "── new admin CRUD resources ───────────────────────────────────────"
for R in applications documents notifications audit-logs student-profiles event-registrations \
         search-queries shortlist-items assistant-conversations brochures research-areas analytics-events; do
  chk "GET  /admin/$R" 200 "$(code "$API/admin/$R" -H "$AUTH")"
done
chk "PUT  /admin/audit-logs/1 blocked" 405 "$(code -X PUT "$API/admin/audit-logs/1" -H "$AUTH" \
  -H 'content-type: application/json' -d '{"action":"TAMPERED"}')"
chk "DEL  /admin/audit-logs/1 blocked" 405 "$(code -X DELETE "$API/admin/audit-logs/1" -H "$AUTH")"

echo "── webhook signature validation ───────────────────────────────────"
chk "POST /whatsapp/webhook bad sig"   401 "$(code -X POST "$API/whatsapp/webhook" \
  -H 'content-type: application/json' -H 'x-hub-signature-256: sha256=deadbeef' -d '{}')"

echo "── rate limiting ──────────────────────────────────────────────────"
LAST=""
for i in $(seq 1 13); do
  LAST=$(code -X POST "$API/admin/auth/login" -H 'content-type: application/json' \
    -d '{"email":"nobody@example.com","password":"wrong"}')
done
chk "11th+ login attempt throttled"    429 "$LAST"
HDR=$(curl -s -D - -o /dev/null "$API/countries" | grep -ci 'x-ratelimit-limit')
chk "X-RateLimit headers present"      1 "$HDR"

echo
echo "────────────────────────────────────────────────────────────────────"
printf 'passed %s   failed %s\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

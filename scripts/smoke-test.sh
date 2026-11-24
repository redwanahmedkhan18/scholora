#!/usr/bin/env bash
# Hit every public route plus the admin shell and report the HTTP status of each.
# Usage: ./scripts/smoke-test.sh [base-url]        default http://127.0.0.1:3000
set -uo pipefail

BASE="${1:-http://127.0.0.1:3000}"
FAIL=0

ROUTES=(
  /
  /study-destinations
  /study-destinations/united-kingdom
  /universities
  /universities/united-states/university-of-texas-at-arlington
  /programs
  /programs/university-of-texas-at-arlington-msc-artificial-intelligence
  /scholarships
  /scholarships/global-excellence-scholarship-united-kingdom
  /phd
  /faculty/dr-amara-chowdhury-university-of-texas-at-arlington
  /faculties/university-of-texas-at-arlington-faculty-of-computer-science
  /services
  /services/career-counseling
  /success-stories
  /success-stories/rahim-ahmed-university-of-texas-at-arlington-2026
  /events
  /blog
  /blog/fall-2027-usa-application-timeline-for-bangladeshi-students
  /faqs
  /contact
  /appointments
  /profile-assessment
  /compare
  /shortlist
  "/search?q=computer"
  /sitemap.xml
  /robots.txt
  /admin
  /admin/homepage-slides
  /admin/marketing
  /admin/settings
  /admin/counselor
  /admin/applications
  /admin/documents
  /admin/notifications
  /admin/audit
  /admin/operations
  /admin/security
  /my/demo-student
)

printf '%-6s %-10s %s\n' CODE BYTES ROUTE
for r in "${ROUTES[@]}"; do
  out=$(curl -s -o /tmp/_smoke -w '%{http_code}' "$BASE$r")
  size=$(wc -c < /tmp/_smoke | tr -d ' ')
  printf '%-6s %-10s %s\n' "$out" "$size" "$r"
  [ "$out" = "200" ] || FAIL=$((FAIL + 1))
done

code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/this-page-does-not-exist")
printf '%-6s %-10s %s\n' "$code" "-" "/this-page-does-not-exist (expect 404)"
[ "$code" = "404" ] || FAIL=$((FAIL + 1))

echo
if [ "$FAIL" -eq 0 ]; then echo "✓ all routes OK"; else echo "✗ $FAIL route(s) failed"; fi
exit "$FAIL"

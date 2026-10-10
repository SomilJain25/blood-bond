# SMS setup (MSG91)

BloodBond sends three kinds of SMS. Create one template for each in the MSG91 dashboard
(Flow / OTP section) and get them DLT-approved (required for India).

| Env var | Purpose | Variables |
|---|---|---|
| `MSG91_OTP_TEMPLATE_ID` | Login OTP | uses MSG91's `##OTP##` |
| `MSG91_DONOR_ALERT_TEMPLATE_ID` | Alert matched donors | `VAR1` blood group, `VAR2` units, `VAR3` hospital, `VAR4` city |
| `MSG91_HOSPITAL_ACCEPT_TEMPLATE_ID` | Tell hospital a donor accepted | `VAR1` donor name, `VAR2` blood group, `VAR3` donor phone |
| `MSG91_BROADCAST_TEMPLATE_ID` | Admin emergency broadcast | `VAR1` the admin's message (max 160 characters) |

Suggested wording (adapt to what DLT approves):

- Donor alert: `BloodBond URGENT: ##VAR1## blood needed (##VAR2## unit) at ##VAR3##, ##VAR4##. Open BloodBond to accept.`
- Hospital: `BloodBond: ##VAR1## (##VAR2##) accepted your request. Call ##VAR3##.`

Then set in `.env`:

    SMS_PROVIDER=msg91
    MSG91_AUTHKEY=...
    DEMO_MODE=false
    SECRET_KEY=<long random string>

With `DEMO_MODE=false` the API refuses to start unless `SECRET_KEY` is changed, and `/auth/otp/send`
returns 503 if SMS is not configured (it never returns the OTP in the response).

Alerts are sent in the background after the request is saved, so a slow or failing SMS provider
never blocks or fails a blood request; failures are logged. I could not test against the live
MSG91 API from my environment: the request format follows MSG91's v5 OTP and Flow APIs and is
covered by tests with the HTTP call mocked, so do one real send with your own number first.

## Admin broadcast

The admin dashboard's broadcast sends one SMS to every *eligible* member of the chosen group
(online, consenting, not in the 56-day lock, not suspended). The recipient count shown in the
dashboard is the number actually messaged. Only one broadcast per minute is allowed, every
broadcast is written to the audit log, and when SMS is not configured the dashboard says that
nothing was sent instead of reporting success. DLT variable length limits apply to `VAR1`;
check what your approved template allows.

## Hospital verification

Admins verify hospitals in the dashboard (Hospitals tab). By default this is only a badge. Set
`REQUIRE_VERIFIED_HOSPITALS=true` to make verification mandatory for raising requests.

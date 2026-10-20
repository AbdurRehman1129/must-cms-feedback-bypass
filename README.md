# MUST CMS Auto Feedback

A small Python script that automatically fills in the course and faculty feedback forms on the MUST CMS student portal, so you can get to your dashboard without clicking through every form.

> **Disclaimer:** This project is for personal convenience and educational purposes. Use it only with your own account, and give feedback you actually stand behind. You are responsible for what is submitted.

## Tutorial Video

<!-- Replace the link below with your video URL -->
https://github.com/user-attachments/assets/541e3d0d-e3b7-430b-bdad-cffe29f38f62

## Features

- Logs in with your roll number and password
- Detects whether feedback is pending (the dashboard redirects to the feedback page only when it is)
- Submits all pending **course** feedback, then all **faculty** feedback
- Fills every question with a rating of your choice (default: Excellent) and a comment
- Handles the last form correctly, where the portal redirects to the dashboard instead of the summary page
- Supports multiple students in one run
- Shows a progress bar and retries on network errors

## Requirements

- Python 3.9 or newer
- The packages `requests`, `beautifulsoup4` and `python-dotenv`

## Installation

```bash
git clone https://github.com/AbdurRehman1129/must-cms-feedback-bypass.git
cd must-cms-feedback-bypass

# optional but recommended
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate

pip install requests beautifulsoup4 python-dotenv
```

## Configuration

1. Copy the example file:

   ```bash
   cp .env.example .env
   ```

   On Windows: `copy .env.example .env`

2. Open `.env` and fill in your details:

   ```env
   ROLL_NO=your_roll_number
   SESSION=your_session
   PROGRAM=your_program
   PASSWORD=your_password
   ```

   Use the same values you pick in the portal login form (Session and Program drop-downs).

### All options

| Variable | Required | Default | Description |
|---|---|---|---|
| `ROLL_NO` | Yes | none | Your roll number |
| `SESSION` | Yes | none | Session value from the login drop-down |
| `PROGRAM` | Yes | none | Program value from the login drop-down |
| `PASSWORD` | Yes | none | Your portal password |
| `PORTAL_BASE_URL` | No | `https://cms.must.edu.pk:8082` | Portal address |
| `VERIFY_SSL` | No | `false` | Set `true` to verify the SSL certificate |
| `REQUEST_DELAY` | No | `0` | Seconds to wait after each request |
| `FEEDBACK_RATING` | No | `rdExcellent` | Radio option applied to every question |
| `FEEDBACK_COMMENT` | No | `very very nice` | Comment added to every form |

### Multiple students

Add more accounts with any prefix, keeping the same four fields for each:

```env
S2_ROLL_NO=
S2_SESSION=
S2_PROGRAM=
S2_PASSWORD=
```

## Usage

```bash
python auto_feedback.py
```

Example output:

```
=== FA22-BSCS-123 ===
Login OK
Course feedback: 6 total, 2 done, 4 pending
Faculty feedback: 6 total, 2 done, 4 pending
[############################] 12/12 (100%)  finished
All feedback completed in 18.4s. Dashboard is accessible.
```

The script exits with code `0` when everything succeeded and `1` otherwise.

## How it works

1. Reads the hidden ASP.NET tokens (`__VIEWSTATE`, `__EVENTVALIDATION`, etc.) from `/login.aspx` and signs in.
2. Requests `/DashBoard.aspx`. If it redirects to `/StudentFeedBackSummary.aspx`, feedback is pending; otherwise there is nothing to do.
3. For each available row, triggers the postback, opens `/SubmitFeedBack.aspx`, fills the form and submits it.
4. Repeats for course feedback (type 1), then faculty feedback (type 2).
5. Checks the dashboard again to confirm nothing is pending.

All tokens are read from the previous response each time, so nothing is hard-coded.

## Troubleshooting

| Problem | What to try |
|---|---|
| `Login failed` | Check `ROLL_NO`, `SESSION`, `PROGRAM` and `PASSWORD` in `.env` |
| `Missing ... in .env` | Every roll number needs its matching `SESSION`, `PROGRAM` and `PASSWORD` |
| SSL errors | Keep `VERIFY_SSL=false` (the default) |
| Timeouts or server errors | Increase `REQUEST_DELAY`, for example to `1` |
| Some rows listed under "Could not submit" | Run the script again; it only handles rows that are still pending |
| The portal layout changed | The script depends on the current page structure and may need updating |

## Security

- Never commit your `.env` file. Add it to `.gitignore`:

  ```gitignore
  .env
  venv/
  __pycache__/
  ```

- Your password is only sent to the portal you configure in `PORTAL_BASE_URL`.

## Contributing

Issues and pull requests are welcome. If the portal changes and the script breaks, please open an issue with the error message.

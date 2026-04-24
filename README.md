# Swim Booker

An automated script to book swim lanes at MyTrilogyLife.com for the next day's 8 AM slot.

## Description

This Python application uses Playwright to automate the process of logging into MyTrilogyLife.com, navigating to the events page, and booking an indoor lap pool reservation for the following day's 8:00 AM time slot. It includes email notifications for success or failure, logging, and error handling with screenshots.

## Features

- Automated login and booking
- Email notifications (success/failure)
- Comprehensive logging
- Screenshot capture on failures
- Environment variable configuration for security
- Headless browser operation

## Prerequisites

- Python 3.8 or higher
- Git
- A Gmail account for email notifications (or modify SMTP settings)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/gds4279/swim-booker.git
   cd swim-booker
   ```

2. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Install Playwright browsers:
   ```bash
   playwright install
   ```

## Configuration

Create a `.env` file in the project root with the following variables:

```env
MYTRILOGY_USERNAME=your_username
MYTRILOGY_PASSWORD=your_password
SMTP_USER=your_gmail@gmail.com
SMTP_APP_PASSWORD=your_app_password
NOTIFY_EMAIL=notification_email@gmail.com  # Optional, defaults to SMTP_USER
```

### Getting a Gmail App Password

1. Go to your Google Account settings
2. Enable 2-Factor Authentication
3. Generate an App Password for "Mail"
4. Use this password in the `SMTP_APP_PASSWORD` variable

## Usage

Run the script:

```bash
python book_swim.py
```

Or use the provided shell script:

```bash
./run.sh
```

The script will:
1. Attempt to book the 8 AM slot for tomorrow
2. Send an email notification with the result
3. Log all actions to `swim_booker.log`

## How It Works

1. **Login**: Navigates to the login page and authenticates using provided credentials
2. **Navigate to Events**: Goes to the events page
3. **Find Event**: Searches for the "Indoor Lap Pool Reservations" event for the next day
4. **Book Slot**: Locates and clicks the 8:00 AM booking button
5. **Confirm**: Handles any confirmation dialogs
6. **Verify**: Checks for success indicators on the page
7. **Notify**: Sends email with results

## Files

- `book_swim.py`: Main application script
- `requirements.txt`: Python dependencies
- `run.sh`: Shell script to run the application
- `.gitignore`: Excludes sensitive files and virtual environments
- `swim_booker.log`: Application logs
- `failure_screenshot.png`: Screenshot captured on booking failure

## Troubleshooting

- **Login Issues**: Verify credentials in `.env`
- **Booking Fails**: Check `swim_booker.log` and `failure_screenshot.png`
- **Email Not Sent**: Confirm SMTP settings and app password
- **Dependencies**: Ensure all packages are installed and Playwright browsers are set up

## Security Notes

- Never commit `.env` files to version control
- Use strong, unique passwords
- Consider using a dedicated email account for notifications

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is for personal use. Please respect the terms of service of MyTrilogyLife.com.
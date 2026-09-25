# Privacy policy

Expense Tracker is a personal, open-source project. It is used by its developer to track his own spending.

## What it accesses
- **Gmail (read-only)**: it searches for transaction emails from Bank Mandiri (`from:bankmandiri.co.id`) and reads them to get the amount, merchant or recipient, and time of each transaction. It cannot send, delete or change emails.
- **Telegram**: it sends messages only to the one chat set in its configuration.

## Where the data goes
- Emails and the transactions parsed from them are stored in a private database on the owner's own computer or server.
- Nothing is sold, shared with third parties, or used for advertising.
- The Google login token stays on that same machine and is never published.

## Removing access
Access can be revoked at any time at https://myaccount.google.com/permissions. Deleting the database removes all stored data.

## Contact
Questions: open an issue on this repository.

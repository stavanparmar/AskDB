# AskDB

AskDB lets you ask plain-English questions about a sample SQLite database.
LangChain uses the live database schema to generate a SQL query; AskDB checks
that it is a single `SELECT`, runs it through a read-only connection with a
200-row cap, and shows the SQL, result table, and a one-sentence summary. It
chooses a bar or line chart when the result contains suitable category/date and
numeric columns. If a query fails, AskDB sends the error back to the model and
retries once.

The sample database is created automatically on first run. It contains 500
generated rows across `customers`, `products`, and `orders`.

## Setup

1. Install Python 3.11.
2. From the project folder, create and activate a virtual environment:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   python -m pip install -r requirements.txt
   ```

4. Create your local environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

   Set `ANTHROPIC_API_KEY` in `.env`. The model defaults to
   `claude-3-5-sonnet-latest`; change `ANTHROPIC_MODEL` there to select another
   Claude model available to your account.

5. Launch the app:

   ```powershell
   streamlit run app.py
   ```

The app opens in your browser and creates `data/askdb.sqlite3` automatically.
That generated database is excluded from Git.

## Example questions

Try questions such as:

1. Which 10 products have the highest total sales?
2. How many orders were placed in each city?
3. Show monthly revenue for the last 12 months.
4. Who are the top five customers by total spending?
5. Which product categories have the most orders?

## Screenshot

An application screenshot can be added at `screenshots/askdb.png`. The image is
not included yet, so this section intentionally avoids a broken image link.

# Hostaway Insights Dashboard

A web-based dashboard application that uses AI to analyze guest reviews and messages, providing quality ratings and actionable insights for each listing.

## Features

- **AI-Powered Analysis**: Uses OpenAI GPT-3.5-turbo to analyze guest feedback
- **Cost-Optimized**: Only analyzes new reviews/messages, avoiding duplicate API calls
- **Quality Ratings**: Categorizes listings as Good, Fair, or Poor
- **Issue Identification**: Identifies top problems facing each listing
- **Actionable Recommendations**: Provides specific action items to address issues
- **Incremental Updates**: Automatically processes only new data when available

## Installation

1. **Install dependencies**:
   ```bash
   cd dashboard
   pip3 install -r requirements.txt
   ```

2. **Set environment variables**:
   ```bash
   export OPENAI_API_KEY="your-openai-api-key"
   ```

3. **Run the dashboard**:
   ```bash
   python3 app.py
   ```

4. **Access the dashboard**:
   Open your browser to `http://127.0.0.1:5000`

## Configuration

Edit `config.py` or set environment variables:

- `OPENAI_API_KEY`: Your OpenAI API key (required)
- `FLASK_HOST`: Host to run on (default: 127.0.0.1)
- `FLASK_PORT`: Port to run on (default: 5000)
- `FLASK_DEBUG`: Enable debug mode (default: False)

## Usage

1. **Select a Listing**: On the home page, browse or search for a listing
2. **View Insights**: Click "View Insights" to see AI-generated analysis
3. **Refresh Analysis**: Use the "Refresh Analysis" button to re-analyze all data

## How It Works

1. **Data Extraction**: Fetches recent reviews (last 6 months) and messages (last 2 months) from the database
2. **Incremental Processing**: Checks which reviews/messages have already been analyzed
3. **AI Analysis**: Sends only new data to OpenAI API for analysis
4. **Insight Merging**: Combines new insights with previously cached insights
5. **Caching**: Stores processed item IDs and overall insights to avoid re-analysis

## Cost Optimization

- Tracks individual reviews and messages that have been analyzed
- Only sends new/unprocessed data to OpenAI API
- Uses GPT-3.5-turbo (cheaper than GPT-4)
- Caches results to avoid duplicate API calls
- Merges new insights with existing insights client-side

## Database

The dashboard uses:
- Main database: `../data/database/hostaway.db` (read-only)
- Cache database: `dashboard/data/ai_cache.db` (created automatically)

## Troubleshooting

- **"OPENAI_API_KEY environment variable is required"**: Set your OpenAI API key as an environment variable
- **"Listing not found"**: Ensure the main database has been synced with listing data
- **No insights displayed**: Check that reviews and messages exist for the listing in the specified time windows


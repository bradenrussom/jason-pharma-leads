# jason-pharma-leads
Tool that uses the clinicaltrials.gov API to find late stage trials and related lead information. 
# README.md
# Pharma Lead Finder

A Flask web application that identifies pharmaceutical companies as potential sales leads based on their clinical trial pipeline and FDA approval likelihood.

## Features

- **Live Clinical Trial Data**: Integrates with ClinicalTrials.gov API
- **FDA Approval Scoring**: Intelligent algorithm scores trials based on phase, status, and timeline
- **Lead Prioritization**: Identifies companies within 6 months of potential market entry
- **Company Intelligence**: Detailed analysis of each company's drug pipeline
- **Export Functionality**: Export qualified leads to CSV format
- **Responsive Dashboard**: Mobile-friendly interface with real-time filtering

## Technology Stack

- **Backend**: Flask (Python)
- **Frontend**: Bootstrap 5, JavaScript
- **Data Source**: ClinicalTrials.gov API
- **Deployment**: Render.com

## Installation

1. Clone this repository
2. Install dependencies: `pip install -r requirements.txt`
3. Run locally: `python app.py`
4. Access at: `http://localhost:5000`

## Deployment

This app is configured for easy deployment on Render.com:

1. Connect your GitHub repository to Render
2. Use the following settings:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Python Version: 3.11.5

## Usage

1. **Dashboard**: View key metrics and statistics
2. **Search**: Load latest clinical trial data
3. **Filter**: Filter leads by phase, priority, or company
4. **Analyze**: Click company names for detailed pipeline analysis
5. **Export**: Download qualified leads as CSV

## Lead Scoring Algorithm

The FDA approval likelihood is calculated based on:

- **Trial Phase**: Phase 3 (40pts), Phase 2/3 (20pts), Phase 4 (50pts)
- **Trial Status**: Completed (30pts), Active not recruiting (25pts), Recruiting (15pts)
- **Timeline**: Bonus points for trials completing within 6 months (35pts) or 1 year (25pts)
- **Maximum Score**: 100%

## Target Users

- Sales professionals in legal, regulatory, and pharmaceutical services
- Business development teams targeting biotech companies
- Investors tracking pharmaceutical pipelines
- Market researchers analyzing drug development trends

## License

MIT License

## Support

For questions or support, please open an issue in this repository.
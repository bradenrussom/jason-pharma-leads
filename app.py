from flask import Flask, render_template, request, jsonify
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
import csv
import io
import os

app = Flask(__name__)

class ClinicalTrialsAPI:
    """Interface for ClinicalTrials.gov API"""
    
    BASE_URL = "https://clinicaltrials.gov/api/query/study_fields"
    
    def __init__(self):
        self.session = requests.Session()
    
    def search_trials(self, **kwargs):
        """Search clinical trials with various filters"""
        params = {
            'expr': kwargs.get('expr', ''),
            'fields': 'NCTId,BriefTitle,Phase,StudyStatus,StartDate,CompletionDate,Sponsor,Collaborator,InterventionName,Condition,LocationFacility,LocationCity,LocationState,LocationCountry',
            'min_rnk': kwargs.get('min_rank', 1),
            'max_rnk': kwargs.get('max_rank', 100),
            'fmt': 'json'
        }
        
        response = self.session.get(self.BASE_URL, params=params)
        return response.json() if response.status_code == 200 else None
    
    def get_late_phase_trials(self, phases=['Phase 3', 'Phase 2/Phase 3', 'Phase 4']):
        """Get trials in late phases"""
        phase_expr = ' OR '.join([f'AREA[Phase]{phase}' for phase in phases])
        return self.search_trials(expr=phase_expr, max_rank=1000)
    
    def get_recent_trials(self, days=180):
        """Get trials started within specified days"""
        start_date = (datetime.now() - timedelta(days=days)).strftime('%m/%d/%Y')
        expr = f'AREA[StartDate]RANGE[{start_date}, MAX]'
        return self.search_trials(expr=expr)

class LeadScorer:
    """Score and rank potential leads"""
    
    @staticmethod
    def calculate_fda_approval_likelihood(trial_data):
        """Calculate likelihood of FDA approval based on trial characteristics"""
        score = 0
        
        # Phase scoring
        phase = trial_data.get('Phase', [''])[0]
        if 'Phase 3' in phase:
            score += 40
        elif 'Phase 2' in phase:
            score += 20
        elif 'Phase 4' in phase:
            score += 50  # Post-market, already approved
        
        # Status scoring
        status = trial_data.get('StudyStatus', [''])[0]
        if status == 'Completed':
            score += 30
        elif status == 'Active, not recruiting':
            score += 25
        elif status == 'Recruiting':
            score += 15
        
        # Timeline scoring (closer to completion = higher score)
        completion_date = trial_data.get('CompletionDate', [''])
        if completion_date and completion_date[0]:
            try:
                comp_date = datetime.strptime(completion_date[0], '%B %d, %Y')
                days_to_completion = (comp_date - datetime.now()).days
                if days_to_completion <= 180:  # 6 months
                    score += 35
                elif days_to_completion <= 365:  # 1 year
                    score += 25
            except:
                pass
        
        return min(score, 100)  # Cap at 100
    
    @staticmethod
    def extract_company_info(trial_data):
        """Extract and clean company information"""
        sponsors = trial_data.get('Sponsor', [])
        collaborators = trial_data.get('Collaborator', [])
        
        companies = []
        for sponsor in sponsors:
            if sponsor and 'University' not in sponsor and 'Hospital' not in sponsor:
                companies.append(sponsor)
        
        for collab in collaborators:
            if collab and 'University' not in collab and 'Hospital' not in collab:
                companies.append(collab)
        
        return list(set(companies))  # Remove duplicates

# Initialize API client
ct_api = ClinicalTrialsAPI()

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('dashboard.html')

@app.route('/api/leads')
def get_leads():
    """Get scored leads from clinical trials data"""
    try:
        # Get late phase trials
        trials_data = ct_api.get_late_phase_trials()
        
        if not trials_data or 'StudyFieldsResponse' not in trials_data:
            return jsonify({'error': 'No data available'}), 500
        
        leads = []
        for trial in trials_data['StudyFieldsResponse']['StudyFields']:
            # Calculate FDA approval likelihood
            likelihood = LeadScorer.calculate_fda_approval_likelihood(trial)
            
            # Extract company info
            companies = LeadScorer.extract_company_info(trial)
            
            if companies and likelihood > 30:  # Only high-potential leads
                lead = {
                    'nct_id': trial.get('NCTId', [''])[0],
                    'title': trial.get('BriefTitle', [''])[0],
                    'phase': trial.get('Phase', [''])[0],
                    'status': trial.get('StudyStatus', [''])[0],
                    'companies': companies,
                    'drug_name': trial.get('InterventionName', [''])[0],
                    'condition': trial.get('Condition', [''])[0],
                    'completion_date': trial.get('CompletionDate', [''])[0],
                    'fda_likelihood': likelihood,
                    'priority': 'High' if likelihood > 70 else 'Medium' if likelihood > 50 else 'Low'
                }
                leads.append(lead)
        
        # Sort by FDA likelihood (highest first)
        leads.sort(key=lambda x: x['fda_likelihood'], reverse=True)
        
        return jsonify(leads[:50])  # Return top 50 leads
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/company/<company_name>')
def get_company_details(company_name):
    """Get detailed information about a specific company"""
    try:
        # Search for all trials by this company
        expr = f'AREA[Sponsor]{company_name}'
        trials_data = ct_api.search_trials(expr=expr, max_rank=200)
        
        if not trials_data or 'StudyFieldsResponse' not in trials_data:
            return jsonify({'error': 'No data available'}), 404
        
        company_trials = []
        for trial in trials_data['StudyFieldsResponse']['StudyFields']:
            trial_info = {
                'nct_id': trial.get('NCTId', [''])[0],
                'title': trial.get('BriefTitle', [''])[0],
                'phase': trial.get('Phase', [''])[0],
                'status': trial.get('StudyStatus', [''])[0],
                'drug_name': trial.get('InterventionName', [''])[0],
                'condition': trial.get('Condition', [''])[0],
                'start_date': trial.get('StartDate', [''])[0],
                'completion_date': trial.get('CompletionDate', [''])[0],
                'fda_likelihood': LeadScorer.calculate_fda_approval_likelihood(trial)
            }
            company_trials.append(trial_info)
        
        return jsonify({
            'company': company_name,
            'total_trials': len(company_trials),
            'trials': company_trials
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/pipeline')
def get_pipeline_analysis():
    """Get pipeline analysis for market timing"""
    try:
        # Get trials completing in next 6 months
        end_date = (datetime.now() + timedelta(days=180)).strftime('%m/%d/%Y')
        current_date = datetime.now().strftime('%m/%d/%Y')
        
        expr = f'AREA[CompletionDate]RANGE[{current_date}, {end_date}] AND (AREA[Phase]Phase 3 OR AREA[Phase]Phase 2/Phase 3)'
        trials_data = ct_api.search_trials(expr=expr, max_rank=500)
        
        if not trials_data or 'StudyFieldsResponse' not in trials_data:
            return jsonify({'error': 'No data available'}), 500
        
        pipeline = []
        for trial in trials_data['StudyFieldsResponse']['StudyFields']:
            companies = LeadScorer.extract_company_info(trial)
            if companies:
                pipeline_item = {
                    'companies': companies,
                    'drug_name': trial.get('InterventionName', [''])[0],
                    'phase': trial.get('Phase', [''])[0],
                    'completion_date': trial.get('CompletionDate', [''])[0],
                    'condition': trial.get('Condition', [''])[0],
                    'urgency': 'High',  # Within 6 months
                    'fda_likelihood': LeadScorer.calculate_fda_approval_likelihood(trial)
                }
                pipeline.append(pipeline_item)
        
        return jsonify(pipeline)
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export')
def export_leads():
    """Export leads to CSV format"""
    try:
        # Get current leads
        leads_response = get_leads()
        leads_data = json.loads(leads_response.data)
        
        # Create CSV in memory
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['nct_id', 'drug_name', 'companies', 'phase', 'status', 'condition', 'completion_date', 'fda_likelihood', 'priority'])
        writer.writeheader()
        
        for lead in leads_data:
            # Convert companies list to string
            lead_copy = lead.copy()
            lead_copy['companies'] = ', '.join(lead['companies'])
            writer.writerow(lead_copy)
        
        return jsonify({'message': 'Export successful', 'data': output.getvalue()})
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # For local development
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
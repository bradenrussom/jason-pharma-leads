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
    """Interface for ClinicalTrials.gov API v2.0"""
    
    BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
    
    def __init__(self):
        self.session = requests.Session()
    
    def search_trials(self, **kwargs):
        """Search clinical trials with various filters using API v2.0"""
        params = {
            'query.term': kwargs.get('query_term', ''),
            'filter.phase': kwargs.get('phase', ''),
            'pageSize': kwargs.get('page_size', 100),
            'format': 'json'
        }
        
        # Remove empty parameters
        params = {k: v for k, v in params.items() if v}
        
        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=30)
            app.logger.info(f"API call: {response.url}")
            app.logger.info(f"Status code: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            else:
                app.logger.error(f"API error: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            app.logger.error(f"API request failed: {e}")
            return None
    
    def get_late_phase_trials(self):
        """Get trials in late phases using API v2.0"""
        # Search for Phase 3 trials
        return self.search_trials(
            query_term='AREA[Phase]PHASE3',
            page_size=200
        )

class LeadScorer:
    """Score and rank potential leads"""
    
    @staticmethod
    def calculate_fda_approval_likelihood(trial_data):
        """Calculate likelihood of FDA approval based on trial characteristics"""
        score = 0
        
        # Get phase from the new API structure
        phases = trial_data.get('protocolSection', {}).get('designModule', {}).get('phases', [])
        phase_str = ', '.join(phases) if phases else ''
        
        # Phase scoring
        if 'PHASE3' in phase_str:
            score += 40
        elif 'PHASE2' in phase_str:
            score += 20
        elif 'PHASE4' in phase_str:
            score += 50
        
        # Status scoring - new API structure
        status = trial_data.get('protocolSection', {}).get('statusModule', {}).get('overallStatus', '')
        if status == 'COMPLETED':
            score += 30
        elif status == 'ACTIVE_NOT_RECRUITING':
            score += 25
        elif status == 'RECRUITING':
            score += 15
        
        # Timeline scoring
        completion_date_info = trial_data.get('protocolSection', {}).get('statusModule', {}).get('completionDateStruct', {})
        if completion_date_info:
            try:
                date_str = completion_date_info.get('date', '')
                if date_str:
                    # Parse date format YYYY-MM-DD
                    comp_date = datetime.strptime(date_str, '%Y-%m-%d')
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
        """Extract and clean company information from new API structure"""
        companies = []
        
        # Get lead sponsor
        lead_sponsor = trial_data.get('protocolSection', {}).get('sponsorCollaboratorsModule', {}).get('leadSponsor', {})
        if lead_sponsor:
            sponsor_name = lead_sponsor.get('name', '')
            if sponsor_name and 'University' not in sponsor_name and 'Hospital' not in sponsor_name:
                companies.append(sponsor_name)
        
        # Get collaborators
        collaborators = trial_data.get('protocolSection', {}).get('sponsorCollaboratorsModule', {}).get('collaborators', [])
        for collab in collaborators:
            collab_name = collab.get('name', '')
            if collab_name and 'University' not in collab_name and 'Hospital' not in collab_name:
                companies.append(collab_name)
        
        return list(set(companies))  # Remove duplicates

# Initialize API client
ct_api = ClinicalTrialsAPI()

@app.route('/api/debug')
def debug_api():
    """Debug endpoint to test API connectivity"""
    try:
        # Test basic API call
        test_url = "https://clinicaltrials.gov/api/query/study_fields"
        test_params = {
            'expr': 'AREA[Phase]Phase 3',
            'fields': 'NCTId,BriefTitle,Phase',
            'max_rnk': 5,
            'fmt': 'json'
        }
        
        response = requests.get(test_url, params=test_params, timeout=30)
        
        return jsonify({
            'status_code': response.status_code,
            'url': response.url,
            'response_size': len(response.text),
            'first_200_chars': response.text[:200],
            'success': response.status_code == 200
        })
        
    except Exception as e:
        return jsonify({
            'error': str(e),
            'error_type': type(e).__name__
        })

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('dashboard.html')

@app.route('/api/leads')
def get_leads():
    """Get scored leads from clinical trials data using API v2.0"""
    app.logger.info("Starting get_leads function...")
    
    try:
        # Get late phase trials
        app.logger.info("Calling ct_api.get_late_phase_trials()...")
        trials_data = ct_api.get_late_phase_trials()
        
        app.logger.info(f"API response type: {type(trials_data)}")
        
        if not trials_data:
            app.logger.error("No trials_data received")
            return jsonify({'error': 'No response from ClinicalTrials.gov API'}), 500
        
        app.logger.info(f"trials_data keys: {trials_data.keys() if isinstance(trials_data, dict) else 'Not a dict'}")
        
        # New API v2.0 structure
        studies = trials_data.get('studies', [])
        app.logger.info(f"Found {len(studies)} studies")
        
        if not studies:
            app.logger.error("No studies found in response")
            return jsonify({'error': 'No studies found', 'response_sample': str(trials_data)[:500]}), 500
        
        leads = []
        for i, study in enumerate(studies):
            try:
                # Calculate FDA approval likelihood
                likelihood = LeadScorer.calculate_fda_approval_likelihood(study)
                
                # Extract company info
                companies = LeadScorer.extract_company_info(study)
                
                if companies and likelihood > 30:  # Only high-potential leads
                    # Extract data from new API structure
                    protocol_section = study.get('protocolSection', {})
                    identification = protocol_section.get('identificationModule', {})
                    status_module = protocol_section.get('statusModule', {})
                    design_module = protocol_section.get('designModule', {})
                    conditions_module = protocol_section.get('conditionsModule', {})
                    interventions_module = protocol_section.get('armsInterventionsModule', {})
                    
                    # Get intervention names
                    interventions = interventions_module.get('interventions', [])
                    intervention_names = [interv.get('name', '') for interv in interventions]
                    drug_name = ', '.join(intervention_names) if intervention_names else 'Unknown'
                    
                    # Get conditions
                    conditions = conditions_module.get('conditions', [])
                    condition = ', '.join(conditions) if conditions else 'Unknown'
                    
                    # Get completion date
                    completion_date_struct = status_module.get('completionDateStruct', {})
                    completion_date = completion_date_struct.get('date', 'TBD')
                    
                    # Get phases
                    phases = design_module.get('phases', [])
                    phase = ', '.join(phases) if phases else 'Unknown'
                    
                    lead = {
                        'nct_id': identification.get('nctId', 'Unknown'),
                        'title': identification.get('briefTitle', 'Unknown'),
                        'phase': phase,
                        'status': status_module.get('overallStatus', 'Unknown'),
                        'companies': companies,
                        'drug_name': drug_name,
                        'condition': condition,
                        'completion_date': completion_date,
                        'fda_likelihood': likelihood,
                        'priority': 'High' if likelihood > 70 else 'Medium' if likelihood > 50 else 'Low'
                    }
                    leads.append(lead)
                    
                if len(leads) >= 50:  # Limit to prevent timeout
                    break
                        
            except Exception as trial_error:
                app.logger.error(f"Error processing study {i}: {trial_error}")
                continue
        
        app.logger.info(f"Generated {len(leads)} leads")
        
        # Sort by FDA likelihood (highest first)
        leads.sort(key=lambda x: x['fda_likelihood'], reverse=True)
        
        return jsonify(leads)
    
    except Exception as e:
        app.logger.error(f"Error in get_leads: {e}")
        import traceback
        app.logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e), 'error_type': type(e).__name__}), 500

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
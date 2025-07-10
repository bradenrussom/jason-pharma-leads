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
    """Debug endpoint to test API connectivity and see data structure"""
    try:
        # Test the new API v2.0
        test_url = "https://clinicaltrials.gov/api/v2/studies"
        test_params = {
            'query.term': 'AREA[Phase]PHASE3',
            'pageSize': 2,
            'format': 'json'
        }
        
        response = requests.get(test_url, params=test_params, timeout=30)
        
        # Also get sample leads data
        leads_response = get_leads()
        
        # Parse leads data
        sample_leads = []
        fields_info = {}
        
        if leads_response.status_code == 200:
            try:
                sample_leads = json.loads(leads_response.data)
                if sample_leads:
                    first_lead = sample_leads[0]
                    fields_info = {
                        'total_leads': len(sample_leads),
                        'fields_in_first_lead': list(first_lead.keys()),
                        'sample_values': {k: str(v)[:100] for k, v in first_lead.items()}  # Truncate long values
                    }
            except Exception as parse_error:
                fields_info = {'parse_error': str(parse_error)}
        
        return jsonify({
            'api_test': {
                'status_code': response.status_code,
                'url': response.url,
                'working': response.status_code == 200,
                'first_100_chars': response.text[:100] if response.text else 'No response'
            },
            'leads_test': {
                'status_code': leads_response.status_code,
                'working': leads_response.status_code == 200,
                'data_info': fields_info
            }
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
    """Get detailed information about a specific company using API v2.0"""
    try:
        app.logger.info(f"Searching for company: {company_name}")
        
        # Search for all trials by this company using API v2.0
        params = {
            'query.term': f'AREA[LeadSponsorName]{company_name}',
            'pageSize': 50,
            'format': 'json'
        }
        
        response = requests.get("https://clinicaltrials.gov/api/v2/studies", params=params, timeout=30)
        
        if response.status_code != 200:
            app.logger.error(f"API request failed: {response.status_code}")
            return jsonify({'error': 'API request failed'}), 500
        
        trials_data = response.json()
        studies = trials_data.get('studies', [])
        
        app.logger.info(f"Found {len(studies)} studies for {company_name}")
        
        if not studies:
            return jsonify({'error': 'No data available'}), 404
        
        company_trials = []
        for study in studies:
            try:
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
                
                # Get phases
                phases = design_module.get('phases', [])
                phase = ', '.join(phases) if phases else 'Unknown'
                
                # Get dates
                start_date_struct = status_module.get('startDateStruct', {})
                start_date = start_date_struct.get('date', 'Unknown')
                
                completion_date_struct = status_module.get('completionDateStruct', {})
                completion_date = completion_date_struct.get('date', 'Unknown')
                
                trial_info = {
                    'nct_id': identification.get('nctId', 'Unknown'),
                    'title': identification.get('briefTitle', 'Unknown'),
                    'phase': phase,
                    'status': status_module.get('overallStatus', 'Unknown'),
                    'drug_name': drug_name,
                    'condition': condition,
                    'start_date': start_date,
                    'completion_date': completion_date,
                    'fda_likelihood': LeadScorer.calculate_fda_approval_likelihood(study)
                }
                company_trials.append(trial_info)
                
            except Exception as trial_error:
                app.logger.error(f"Error processing trial: {trial_error}")
                continue
        
        return jsonify({
            'company': company_name,
            'total_trials': len(company_trials),
            'trials': company_trials
        })
    
    except Exception as e:
        app.logger.error(f"Error in get_company_details: {e}")
        import traceback
        app.logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/pipeline')
def get_pipeline_analysis():
    """Get pipeline analysis for market timing using API v2.0"""
    try:
        app.logger.info("Getting pipeline analysis...")
        
        # Get Phase 3 trials (already filtered by phase in main search)
        params = {
            'query.term': 'AREA[Phase]PHASE3',
            'pageSize': 100,
            'format': 'json'
        }
        
        response = requests.get("https://clinicaltrials.gov/api/v2/studies", params=params, timeout=30)
        
        if response.status_code != 200:
            return jsonify({'error': 'API request failed'}), 500
        
        trials_data = response.json()
        studies = trials_data.get('studies', [])
        
        pipeline = []
        for study in studies:
            try:
                protocol_section = study.get('protocolSection', {})
                status_module = protocol_section.get('statusModule', {})
                
                # Check if completing within 6 months
                completion_date_struct = status_module.get('completionDateStruct', {})
                completion_date = completion_date_struct.get('date', '')
                
                within_6_months = False
                if completion_date:
                    try:
                        comp_date = datetime.strptime(completion_date, '%Y-%m-%d')
                        days_to_completion = (comp_date - datetime.now()).days
                        within_6_months = days_to_completion <= 180
                    except:
                        pass
                
                if within_6_months:
                    companies = LeadScorer.extract_company_info(study)
                    if companies:
                        # Get other study details
                        identification = protocol_section.get('identificationModule', {})
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
                        
                        # Get phases
                        phases = design_module.get('phases', [])
                        phase = ', '.join(phases) if phases else 'Unknown'
                        
                        pipeline_item = {
                            'companies': companies,
                            'drug_name': drug_name,
                            'phase': phase,
                            'completion_date': completion_date,
                            'condition': condition,
                            'urgency': 'High',  # Within 6 months
                            'fda_likelihood': LeadScorer.calculate_fda_approval_likelihood(study)
                        }
                        pipeline.append(pipeline_item)
                        
            except Exception as study_error:
                app.logger.error(f"Error processing pipeline study: {study_error}")
                continue
        
        app.logger.info(f"Found {len(pipeline)} pipeline items")
        return jsonify(pipeline)
    
    except Exception as e:
        app.logger.error(f"Error in get_pipeline_analysis: {e}")
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
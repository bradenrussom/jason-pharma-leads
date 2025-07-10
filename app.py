# CLEAN VERSION 6.0 - No Duplicates
from flask import Flask, render_template, request, jsonify, Response
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict
import io
import os
import sys
import logging

app = Flask(__name__)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
app.logger.setLevel(logging.INFO)

class ClinicalTrialsAPI:
    """Interface for ClinicalTrials.gov API v2.0"""
    
    BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
    
    def __init__(self):
        self.session = requests.Session()
    
    def get_late_phase_trials(self):
        """Get trials in late phases using API v2.0"""
        params = {
            'query.term': 'AREA[Phase]PHASE3',
            'pageSize': 200,
            'format': 'json'
        }
        
        try:
            response = self.session.get(self.BASE_URL, params=params, timeout=30)
            app.logger.info(f"API call: {response.url}")
            app.logger.info(f"Status code: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            else:
                app.logger.error(f"API error: {response.status_code}")
                return None
        except Exception as e:
            app.logger.error(f"API request failed: {e}")
            return None

class LeadScorer:
    """Score and rank potential leads"""
    
    @staticmethod
    def calculate_fda_approval_likelihood(trial_data):
        """Calculate likelihood of FDA approval"""
        score = 0
        
        # Get phase
        phases = trial_data.get('protocolSection', {}).get('designModule', {}).get('phases', [])
        phase_str = ', '.join(phases) if phases else ''
        
        if 'PHASE3' in phase_str:
            score += 40
        elif 'PHASE2' in phase_str:
            score += 20
        elif 'PHASE4' in phase_str:
            score += 50
        
        # Get status
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
                    comp_date = datetime.strptime(date_str, '%Y-%m-%d')
                    days_to_completion = (comp_date - datetime.now()).days
                    if days_to_completion <= 180:
                        score += 35
                    elif days_to_completion <= 365:
                        score += 25
            except:
                pass
        
        return min(score, 100)
    
    @staticmethod
    def extract_company_info(trial_data):
        """Extract company information"""
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
        
        return list(set(companies))

# Initialize API client
ct_api = ClinicalTrialsAPI()

@app.route('/')
def index():
    """Main dashboard"""
    return render_template('dashboard.html')

@app.route('/api/debug')
def debug_api():
    """Debug endpoint"""
    try:
        test_url = "https://clinicaltrials.gov/api/v2/studies"
        test_params = {
            'query.term': 'AREA[Phase]PHASE3',
            'pageSize': 2,
            'format': 'json'
        }
        
        response = requests.get(test_url, params=test_params, timeout=30)
        
        # Get sample leads
        leads_response = get_leads()
        
        fields_info = {}
        if leads_response.status_code == 200:
            try:
                sample_leads = json.loads(leads_response.data)
                if sample_leads:
                    first_lead = sample_leads[0]
                    fields_info = {
                        'total_leads': len(sample_leads),
                        'fields_in_first_lead': list(first_lead.keys()),
                        'sample_values': {k: str(v)[:100] for k, v in first_lead.items()}
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

@app.route('/api/export-test')
def export_leads_test():
    """Test export function"""
    try:
        app.logger.info("TEST EXPORT: Starting...")
        
        csv_content = "NCT ID,Company,Drug,Priority\n"
        csv_content += "NCT12345,Test Company,Test Drug,High\n"
        csv_content += "NCT67890,Another Company,Another Drug,Medium\n"
        
        app.logger.info("TEST EXPORT: Generated test CSV")
        
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=test_export.csv'}
        )
        
    except Exception as e:
        app.logger.error(f"TEST EXPORT ERROR: {e}")
        return jsonify({'error': f'Test export error: {str(e)}'}), 500

@app.route('/api/leads')
def get_leads():
    """Get scored leads from clinical trials data"""
    app.logger.info("Starting get_leads function...")
    
    try:
        # Get late phase trials
        trials_data = ct_api.get_late_phase_trials()
        
        if not trials_data:
            app.logger.error("No trials_data received")
            return jsonify({'error': 'No response from ClinicalTrials.gov API'}), 500
        
        # New API v2.0 structure
        studies = trials_data.get('studies', [])
        app.logger.info(f"Found {len(studies)} studies")
        
        if not studies:
            return jsonify({'error': 'No studies found'}), 500
        
        leads = []
        for i, study in enumerate(studies):
            try:
                # Calculate FDA approval likelihood
                likelihood = LeadScorer.calculate_fda_approval_likelihood(study)
                
                # Extract company info
                companies = LeadScorer.extract_company_info(study)
                
                if companies and likelihood > 30:
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
                    
                if len(leads) >= 50:
                    break
                        
            except Exception as trial_error:
                app.logger.error(f"Error processing study {i}: {trial_error}")
                continue
        
        app.logger.info(f"Generated {len(leads)} leads")
        
        # Sort by FDA likelihood
        leads.sort(key=lambda x: x['fda_likelihood'], reverse=True)
        
        return jsonify(leads)
    
    except Exception as e:
        app.logger.error(f"Error in get_leads: {e}")
        import traceback
        app.logger.error(f"Full traceback: {traceback.format_exc()}")
        return jsonify({'error': str(e), 'error_type': type(e).__name__}), 500

@app.route('/api/export')
def export_leads():
    """Export leads - NO CSV LIBRARY"""
    app.logger.info("CLEAN EXPORT: Starting...")
    
    try:
        # Get leads data
        leads_response = get_leads()
        
        if leads_response.status_code != 200:
            return jsonify({'error': 'Could not fetch leads'}), 500
            
        leads_data = json.loads(leads_response.data)
        
        if not leads_data:
            return jsonify({'error': 'No leads found'}), 400
        
        # Build as pipe-separated text file
        lines = []
        lines.append("NCT_ID|Title|Drug|Companies|Phase|Status|Condition|Completion|FDA_Score|Priority")
        
        for lead in leads_data:
            line = f"{lead.get('nct_id', '')}|{lead.get('title', '')}|{lead.get('drug_name', '')}|{lead.get('companies', '')}|{lead.get('phase', '')}|{lead.get('status', '')}|{lead.get('condition', '')}|{lead.get('completion_date', '')}|{lead.get('fda_likelihood', '')}|{lead.get('priority', '')}"
            lines.append(line)
        
        content = "\n".join(lines)
        
        return Response(
            content,
            mimetype='text/plain',
            headers={'Content-Disposition': 'attachment; filename=leads.txt'}
        )
        
    except Exception as e:
        app.logger.error(f"CLEAN EXPORT ERROR: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/company/<company_name>')
def get_company_details(company_name):
    """Get company details"""
    try:
        params = {
            'query.term': f'AREA[LeadSponsorName]{company_name}',
            'pageSize': 50,
            'format': 'json'
        }
        
        response = requests.get("https://clinicaltrials.gov/api/v2/studies", params=params, timeout=30)
        
        if response.status_code != 200:
            return jsonify({'error': 'API request failed'}), 500
        
        trials_data = response.json()
        studies = trials_data.get('studies', [])
        
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
                
                interventions = interventions_module.get('interventions', [])
                intervention_names = [interv.get('name', '') for interv in interventions]
                drug_name = ', '.join(intervention_names) if intervention_names else 'Unknown'
                
                conditions = conditions_module.get('conditions', [])
                condition = ', '.join(conditions) if conditions else 'Unknown'
                
                phases = design_module.get('phases', [])
                phase = ', '.join(phases) if phases else 'Unknown'
                
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
        return jsonify({'error': str(e)}), 500

@app.route('/api/pipeline')
def get_pipeline_analysis():
    """Get pipeline analysis"""
    try:
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
                        identification = protocol_section.get('identificationModule', {})
                        design_module = protocol_section.get('designModule', {})
                        conditions_module = protocol_section.get('conditionsModule', {})
                        interventions_module = protocol_section.get('armsInterventionsModule', {})
                        
                        interventions = interventions_module.get('interventions', [])
                        intervention_names = [interv.get('name', '') for interv in interventions]
                        drug_name = ', '.join(intervention_names) if intervention_names else 'Unknown'
                        
                        conditions = conditions_module.get('conditions', [])
                        condition = ', '.join(conditions) if conditions else 'Unknown'
                        
                        phases = design_module.get('phases', [])
                        phase = ', '.join(phases) if phases else 'Unknown'
                        
                        pipeline_item = {
                            'companies': companies,
                            'drug_name': drug_name,
                            'phase': phase,
                            'completion_date': completion_date,
                            'condition': condition,
                            'urgency': 'High',
                            'fda_likelihood': LeadScorer.calculate_fda_approval_likelihood(study)
                        }
                        pipeline.append(pipeline_item)
                        
            except Exception as study_error:
                app.logger.error(f"Error processing pipeline study: {study_error}")
                continue
        
        return jsonify(pipeline)
    
    except Exception as e:
        app.logger.error(f"Error in get_pipeline_analysis: {e}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
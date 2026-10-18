"""Observed weight plus explicitly declared profile height; no default biometrics.

CDC: adult BMI categories apply from age 20, independently of sex.
https://www.cdc.gov/bmi/faq/
"""
from datetime import date
import math

SOURCE = 'https://www.cdc.gov/bmi/faq/'
NOTE = 'L’IMC non distingue massa muscolare e grasso e non misura la prestazione sportiva.'


def body_mass_index(profile, weight, as_of):
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    latest = weight.get('latest')
    height = profile.get('height_cm')
    birth = date.fromisoformat(profile['birth_date']) if profile.get('birth_date') else None
    measured_on = date.fromisoformat(latest['date']) if latest else as_of
    age = (measured_on.year-birth.year-((measured_on.month, measured_on.day)<(birth.month, birth.day))) if birth and measured_on >= birth else None
    result = {'value': None, 'unit': 'kg/m²', 'height_cm': height, 'height_source': 'profile' if height else None,
              'weight_kg': latest['value'] if latest else None, 'weight_date': latest['date'] if latest else None,
              'weight_source': latest.get('source') if latest else None, 'age_years': age,
              'age_basis_date': str(measured_on), 'sex': profile.get('sex'), 'classification': None,
              'stale': bool(weight.get('stale')), 'missing_fields': [], 'note': NOTE,
              'formula': 'peso_kg / (altezza_cm / 100)²', 'source_url': SOURCE}
    if height is None:
        result['missing_fields'].append('height_cm')
    if not latest:
        result['missing_fields'].append('weight_kg')
    if result['missing_fields']:
        result['reason'] = 'Imposta l’altezza nel profilo per calcolare l’IMC.' if height is None else 'Serve una pesata registrata per calcolare l’IMC.'
        return result
    value = latest['value']
    if isinstance(height, bool) or isinstance(value, bool) or not isinstance(height, (int,float)) or not isinstance(value, (int,float)) or not math.isfinite(height) or not math.isfinite(value) or not 50 <= height <= 250 or value <= 0 or measured_on > as_of or (birth and measured_on < birth) or latest.get('source') == 'local_estimate':
        result['reason'] = 'Peso o altezza non utilizzabili per questa data.'
        return result
    bmi = value/(height/100)**2
    if age is not None and age < 2:
        result['reason'] = 'Prima dei 2 anni l’IMC non viene interpretato con questo strumento.'
        return result
    result['value'] = round(bmi, 2)
    if age is None:
        result['missing_fields'].append('birth_date')
        result['reason'] = 'Aggiungi la data di nascita per scegliere il riferimento adatto all’età.'
    elif age < 20:
        result['reason'] = 'Prima dei 20 anni servono riferimenti IMC per età e sesso: la classificazione per adulti non viene applicata.'
    elif weight.get('stale'):
        result['reason'] = 'Peso da aggiornare: IMC riferito alla data della pesata indicata.'
    else:
        code, label = ('underweight', 'Sottopeso') if bmi < 18.5 else ('reference', 'Intervallo di riferimento adulti') if bmi < 25 else ('overweight', 'Sovrappeso') if bmi < 30 else ('obesity_1', 'Obesità · classe 1') if bmi < 35 else ('obesity_2', 'Obesità · classe 2') if bmi < 40 else ('obesity_3', 'Obesità · classe 3')
        result['classification'] = {'code': code, 'label': label}
        result['reason'] = 'Riferimento CDC per adulti da 20 anni: le soglie non cambiano con sesso o età.'
    return result

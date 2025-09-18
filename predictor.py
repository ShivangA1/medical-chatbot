import pandas as pd
import numpy as np
import csv
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn import preprocessing
from sklearn.calibration import CalibratedClassifierCV
from difflib import get_close_matches

# Load training data
training = pd.read_csv("Data/Training.csv")
cols = training.columns[:-1]
x = training[cols]
y = training["prognosis"]

# Encode target labels
le = preprocessing.LabelEncoder()
y_encoded = le.fit_transform(y)

# Train Random Forest with calibration
x_train, x_test, y_train, y_test = train_test_split(x, y_encoded, test_size=0.33, random_state=42)
rf = RandomForestClassifier(n_estimators=200, random_state=42)
clf = CalibratedClassifierCV(rf, method='isotonic')
clf.fit(x_train, y_train)

# Load dictionaries
severityDictionary, description_list, precautionDictionary = {}, {}, {}

def load_dictionaries():
    try:
        with open("Data/Symptom_severity.csv") as f:
            for row in csv.reader(f):
                if row: severityDictionary[row[0].strip().lower()] = int(row[1])
        with open("Data/symptom_Description.csv") as f:
            for row in csv.reader(f):
                if row: description_list[row[0].strip().lower()] = row[1]
        with open("Data/symptom_precaution.csv") as f:
            for row in csv.reader(f):
                if row: precautionDictionary[row[0].strip().lower()] = row[1:5]
    except Exception as e:
        print(f"⚠️ Dictionary load failed: {e}")

load_dictionaries()

# Map symptoms to indices
symptoms_dict = {symptom.lower(): idx for idx, symptom in enumerate(cols)}

# Quick autocomplete matcher
def suggest_symptoms(partial, n=5):
    partial = partial.strip().lower()
    if not partial:
        return []
    matches = get_close_matches(partial, symptoms_dict.keys(), n=n, cutoff=0.5)
    return matches

# Severity calc (adjusted to avoid huge inflation by days)
def calc_severity(symptoms, days):
    score = sum(severityDictionary.get(symptom, 0) for symptom in symptoms)
    severity = score + min(days, 7) * 0.5
    if severity > 13:
        return "high"
    elif severity > 6:
        return "moderate"
    else:
        return "low"

# Disease prediction
def sec_predict(symptoms):
    input_vector = np.zeros(len(symptoms_dict))
    for symptom in symptoms:
        if symptom in symptoms_dict:
            input_vector[symptoms_dict[symptom]] = 1

    prediction = clf.predict([input_vector])[0]
    proba = clf.predict_proba([input_vector])[0]
    confidence = round(np.max(proba) * 100, 1)
    disease = le.inverse_transform([prediction])[0]

    # Lower bound on small inputs
    if len(symptoms) < 3 and confidence > 80:
        confidence = 80
    return disease, confidence

# Smarter follow-ups
def suggest_followup(symptoms, top_n=3):
    remaining = [s for s in symptoms_dict if s not in symptoms]
    # Score by severity + frequency of occurrence across predicted diseases
    ranked = sorted(
        remaining,
        key=lambda s: severityDictionary.get(s, 0) + sum(training[s]),
        reverse=True
    )
    return ranked[:top_n]

# Main interface
def predict_disease(symptoms, days):
    symptoms = [s.strip().replace(" ", "_").lower() for s in symptoms]
    matched = [s for s in symptoms if s in symptoms_dict]
    if not matched:
        return {"error": "No valid symptoms found."}

    disease, confidence = sec_predict(matched)
    description = description_list.get(disease.lower(), "No description available.")
    precautions = precautionDictionary.get(disease.lower(), ["No precautions found."])
    severity = calc_severity(matched, days)

    result = {
        "disease": disease,
        "description": description,
        "precautions": precautions,
        "severity": severity,
        "confidence": confidence,
    }

    if confidence < 70 or len(matched) < 3:
        result["followup"] = suggest_followup(matched, top_n=3)
    return result

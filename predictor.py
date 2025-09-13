import pandas as pd
import numpy as np
import csv
import re
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn import preprocessing
import logging

logging.basicConfig(level=logging.INFO)

# -------------------------
# Load training data
# -------------------------
training = pd.read_csv("Data/Training.csv")
cols = training.columns[:-1]
x = training[cols]
y = training["prognosis"]

# -------------------------
# Encode target labels
# -------------------------
le = preprocessing.LabelEncoder()
le.fit(y)
y_encoded = le.transform(y)

# -------------------------
# Train Decision Tree
# -------------------------
x_train, x_test, y_train, y_test = train_test_split(x, y_encoded, test_size=0.33, random_state=42)
clf = DecisionTreeClassifier()
clf.fit(x_train, y_train)

# -------------------------
# Load dictionaries
# -------------------------
severityDictionary = {}
description_list = {}
precautionDictionary = {}

def load_dictionaries():
    with open("Data/Symptom_severity.csv") as f:
        for row in csv.reader(f):
            if row: severityDictionary[row[0].lower()] = int(row[1])
    with open("Data/symptom_Description.csv") as f:
        for row in csv.reader(f):
            if row: description_list[row[0].lower()] = row[1]
    with open("Data/symptom_precaution.csv") as f:
        for row in csv.reader(f):
            if row: precautionDictionary[row[0].lower()] = row[1:5]

load_dictionaries()

# -------------------------
# Map symptoms to indices
# -------------------------
symptoms_dict = {symptom.lower(): idx for idx, symptom in enumerate(cols)}

# -------------------------
# Precompute symptom importance
# -------------------------
symptom_importance = {}
for symptom in cols:
    counts = training.groupby('prognosis')[symptom].sum().to_dict()
    symptom_importance[symptom.lower()] = counts

# -------------------------
# Text normalization
# -------------------------
def normalize_symptom(text):
    text = text.strip().lower()
    text = re.sub(r'\s+', '_', text)
    text = re.sub(r'[^a-z_]', '', text)
    return text

# -------------------------
# Severity calculation
# -------------------------
def calc_severity(symptoms, days):
    score = sum(severityDictionary.get(symptom, 0) for symptom in symptoms)
    severity_value = (score * days) / (len(symptoms) + 1)
    if severity_value > 20:
        return "very high"
    elif severity_value > 13:
        return "high"
    else:
        return "moderate"

# -------------------------
# Predict disease
# -------------------------
def sec_predict(symptoms):
    input_vector = np.zeros(len(symptoms_dict))
    for symptom in symptoms:
        if symptom in symptoms_dict:
            input_vector[symptoms_dict[symptom]] = 1
    prediction = clf.predict([input_vector])[0]
    proba = clf.predict_proba([input_vector])[0]
    confidence = round(np.max(proba) * 100, 1)
    disease = le.inverse_transform([prediction])[0]
    logging.info(f"Predicted {disease} with {confidence}% confidence for symptoms {symptoms}")
    return disease, confidence

# -------------------------
# Suggest follow-up symptoms
# -------------------------
def suggest_followup(symptoms, top_n=3):
    remaining = [s for s in symptoms_dict if s not in symptoms]
    ranked = sorted(
        remaining,
        key=lambda s: severityDictionary.get(s, 0) + max(symptom_importance[s].values()),
        reverse=True
    )
    return ranked[:top_n] if ranked else []

# -------------------------
# Main disease prediction
# -------------------------
def predict_disease(symptoms, days=2):
    symptoms = [normalize_symptom(s) for s in symptoms]
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
        "confidence": confidence
    }

    if confidence < 70:
        result["followup"] = suggest_followup(matched, top_n=3)

    return result

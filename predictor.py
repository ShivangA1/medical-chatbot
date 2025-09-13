import pandas as pd
import numpy as np
import csv
import re
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn import preprocessing
import random

# Load training data
training = pd.read_csv("Data/Training.csv")
testing = pd.read_csv("Data/Testing.csv")
cols = training.columns[:-1]
x = training[cols]
y = training["prognosis"]

# Encode target labels
le = preprocessing.LabelEncoder()
le.fit(y)
y_encoded = le.transform(y)

# Train RandomForest model (better probability estimates than DecisionTree)
x_train, x_test, y_train, y_test = train_test_split(
    x, y_encoded, test_size=0.33, random_state=42
)
clf = RandomForestClassifier(n_estimators=200, random_state=42)
clf.fit(x_train, y_train)

# Load dictionaries
severityDictionary = {}
description_list = {}
precautionDictionary = {}

def load_dictionaries():
    with open("Data/Symptom_severity.csv") as f:
        for row in csv.reader(f):
            if row:
                severityDictionary[row[0]] = int(row[1])

    with open("Data/symptom_Description.csv") as f:
        for row in csv.reader(f):
            if row:
                description_list[row[0]] = row[1]

    with open("Data/symptom_precaution.csv") as f:
        for row in csv.reader(f):
            if row:
                precautionDictionary[row[0]] = row[1:5]

load_dictionaries()

# Map symptoms to indices
symptoms_dict = {symptom: idx for idx, symptom in enumerate(cols)}

def calc_severity(symptoms, days):
    score = sum(severityDictionary.get(symptom, 0) for symptom in symptoms)
    severity = (score * days) / (len(symptoms) + 1)
    return "high" if severity > 13 else "moderate"

def sec_predict(symptoms):
    input_vector = np.zeros(len(symptoms_dict))
    for symptom in symptoms:
        if symptom in symptoms_dict:
            input_vector[symptoms_dict[symptom]] = 1
    prediction_proba = clf.predict_proba([input_vector])[0]
    top_indices = prediction_proba.argsort()[::-1][:3]  # top 3 predictions
    diseases = le.inverse_transform(top_indices)
    confidences = prediction_proba[top_indices] * 100

    return list(zip(diseases, confidences))

def suggest_followups(primary_disease):
    """Suggest additional symptoms related to the predicted disease"""
    # Just a simple placeholder: random sample of known symptoms
    followup_candidates = list(symptoms_dict.keys())
    return random.sample(followup_candidates, 3)

def predict_disease(symptoms, days):
    symptoms = [s.strip().replace(" ", "_").lower() for s in symptoms]
    matched = [s for s in symptoms if s in symptoms_dict]

    if not matched:
        return {"error": "No valid symptoms found."}

    predictions = sec_predict(matched)
    primary_disease, confidence = predictions[0]

    # Confidence smoothing for short inputs
    if len(matched) < 3:
        confidence = min(confidence, 70.0)

    # Severity calculation
    severity = calc_severity(matched, days)

    # Disease details
    description = description_list.get(primary_disease, "No description available.")
    precautions = precautionDictionary.get(primary_disease, ["No precautions found."])

    result = {
        "disease": primary_disease,
        "confidence": round(confidence, 2),
        "description": description,
        "precautions": precautions,
        "severity": severity,
    }

    # If confidence too low → suggest follow-ups
    if confidence < 70:
        result["followup"] = suggest_followups(primary_disease)

    return result

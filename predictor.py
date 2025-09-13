import pandas as pd
import numpy as np
import csv
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn import preprocessing

# Load training/testing data
try:
    training = pd.read_csv("Data/Training.csv")
    testing = pd.read_csv("Data/Testing.csv")
except FileNotFoundError:
    raise RuntimeError("❌ Training/Testing data not found in Data/ folder.")

cols = training.columns[:-1]
x = training[cols]
y = training["prognosis"]

# Encode target labels
le = preprocessing.LabelEncoder()
le.fit(y)
y_encoded = le.transform(y)

# Train model
x_train, x_test, y_train, y_test = train_test_split(x, y_encoded, test_size=0.33, random_state=42)
clf = DecisionTreeClassifier()
clf.fit(x_train, y_train)

# Load dictionaries
severityDictionary = {}
description_list = {}
precautionDictionary = {}

def load_dictionaries():
    with open("Data/Symptom_severity.csv") as f:
        for row in csv.reader(f):
            if row: severityDictionary[row[0]] = int(row[1])

    with open("Data/symptom_Description.csv") as f:
        for row in csv.reader(f):
            if row: description_list[row[0]] = row[1]

    with open("Data/symptom_precaution.csv") as f:
        for row in csv.reader(f):
            if row: precautionDictionary[row[0]] = row[1:5]

load_dictionaries()

# Map symptoms to indices
symptoms_dict = {symptom: idx for idx, symptom in enumerate(cols)}

def calc_severity(symptoms, days):
    score = sum(severityDictionary.get(symptom, 0) for symptom in symptoms)
    severity = (score * days) / (len(symptoms) + 1)
    if severity < 5: return "low"
    elif severity < 10: return "moderate"
    elif severity < 15: return "high"
    else: return "critical"

def sec_predict(symptoms):
    input_vector = np.zeros(len(symptoms_dict))
    for symptom in symptoms:
        if symptom in symptoms_dict:
            input_vector[symptoms_dict[symptom]] = 1
    probs = clf.predict_proba([input_vector])[0]
    top_indices = probs.argsort()[-3:][::-1]
    return [(le.inverse_transform([i])[0], round(probs[i]*100, 2)) for i in top_indices]

def predict_disease(symptoms, days):
    symptoms = [s.strip().replace(" ", "_").lower() for s in symptoms]
    matched = [s for s in symptoms if s in symptoms_dict]

    if not matched:
        return {"error": "No valid symptoms found."}

    predictions = sec_predict(matched)
    top_disease, confidence = predictions[0]

    # If confidence is too low → suggest follow-up
    if confidence < 70:
        # Pick 3 possible next symptoms from severity dict (can be improved dynamically)
        follow_up_symptoms = ["nausea", "vomiting", "blurred_vision", "sensitivity_to_light"]
        return {"follow_up": follow_up_symptoms, "predictions": predictions}

    description = description_list.get(top_disease, "No description available.")
    precautions = precautionDictionary.get(top_disease, ["No precautions found."])
    severity = calc_severity(matched, days)

    return {
        "disease": top_disease,
        "confidence": confidence,
        "description": description,
        "precautions": precautions,
        "severity": severity,
        "matched_symptoms": matched,
        "predictions": predictions
    }

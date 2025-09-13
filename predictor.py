import pandas as pd
import numpy as np
import csv
import re
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn import preprocessing

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

# Train Decision Tree model
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
    return "high" if severity > 13 else "moderate"

def sec_predict(symptoms):
    input_vector = np.zeros(len(symptoms_dict))
    for symptom in symptoms:
        if symptom in symptoms_dict:
            input_vector[symptoms_dict[symptom]] = 1
    prediction = clf.predict([input_vector])
    return le.inverse_transform(prediction)[0]

def predict_disease(symptoms, days):
    symptoms = [s.strip().replace(" ", "_").lower() for s in symptoms]
    matched = [s for s in symptoms if s in symptoms_dict]

    if not matched:
        return {"error": "No valid symptoms found."}

    disease = sec_predict(matched)
    description = description_list.get(disease, "No description available.")
    precautions = precautionDictionary.get(disease, ["No precautions found."])
    severity = calc_severity(matched, days)

    return {
        "disease": disease,
        "description": description,
        "precautions": precautions,
        "severity": severity
    }
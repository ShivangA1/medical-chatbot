"""
Upgraded predictor.py

- Loads training data from Data/*.csv
- Trains DecisionTree if no saved model exists, saves to Data/model.pkl
- Returns top-3 predictions with confidence scores
- If top confidence < threshold, suggests dynamic follow-up symptoms that best discriminate
  between top candidate conditions
- Improved severity categorization
"""

import os
import csv
import joblib
import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier
from sklearn.model_selection import train_test_split
from sklearn import preprocessing

DATA_DIR = "Data"
MODEL_PATH = os.path.join(DATA_DIR, "model.pkl")
TRAIN_CSV = os.path.join(DATA_DIR, "Training.csv")
TEST_CSV = os.path.join(DATA_DIR, "Testing.csv")
SEVERITY_CSV = os.path.join(DATA_DIR, "Symptom_severity.csv")
DESC_CSV = os.path.join(DATA_DIR, "symptom_Description.csv")
PRECAUTION_CSV = os.path.join(DATA_DIR, "symptom_precaution.csv")

# Config
CONFIDENCE_THRESHOLD = 70.0  # percent
TOP_K = 3  # top-K predictions to return
FOLLOWUP_COUNT = 3  # how many follow-up symptoms to suggest

# Globals
clf = None
le = None
symptoms_dict = {}
severityDictionary = {}
description_list = {}
precautionDictionary = {}
cols = None
training_df = None

# -------------------------
# Utilities: load CSVs
# -------------------------
def safe_read_csv(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required file not found: {path}")
    return pd.read_csv(path)

def load_aux_dicts():
    global severityDictionary, description_list, precautionDictionary
    severityDictionary = {}
    description_list = {}
    precautionDictionary = {}

    if os.path.exists(SEVERITY_CSV):
        with open(SEVERITY_CSV, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row: 
                    continue
                key = row[0].strip().replace(" ", "_").lower()
                try:
                    severityDictionary[key] = int(row[1])
                except Exception:
                    severityDictionary[key] = 0

    if os.path.exists(DESC_CSV):
        with open(DESC_CSV, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                name = row[0].strip()
                description_list[name] = row[1] if len(row) > 1 else "No description available."

    if os.path.exists(PRECAUTION_CSV):
        with open(PRECAUTION_CSV, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                if not row:
                    continue
                key = row[0].strip()
                precautionDictionary[key] = row[1:5] if len(row) > 1 else ["No precautions found."]

# -------------------------
# Train / Load model
# -------------------------
def train_and_save_model():
    global clf, le, cols, symptoms_dict, training_df
    training = safe_read_csv(TRAIN_CSV)
    testing = safe_read_csv(TEST_CSV)  # not used for training here, kept for consistency
    training_df = training

    cols = training.columns[:-1]
    X = training[cols]
    y = training["prognosis"]

    le_local = preprocessing.LabelEncoder()
    y_encoded = le_local.fit_transform(y)

    X_train, X_test, y_train, y_test = train_test_split(X, y_encoded, test_size=0.33, random_state=42)
    clf_local = DecisionTreeClassifier(random_state=42)
    clf_local.fit(X_train, y_train)

    # Persist
    joblib.dump({"clf": clf_local, "le": le_local, "cols": list(cols)}, MODEL_PATH)
    clf = clf_local
    le = le_local
    cols = list(cols)
    symptoms_dict = {symptom: idx for idx, symptom in enumerate(cols)}
    return clf, le

def load_model():
    global clf, le, cols, symptoms_dict, training_df
    if os.path.exists(MODEL_PATH):
        try:
            data = joblib.load(MODEL_PATH)
            clf = data["clf"]
            le = data["le"]
            cols = list(data["cols"])
            symptoms_dict = {symptom: idx for idx, symptom in enumerate(cols)}
            # Try to load training df for dynamic followup calculation if available
            if os.path.exists(TRAIN_CSV):
                training_df = safe_read_csv(TRAIN_CSV)
            return clf, le
        except Exception:
            # fallback to training if load fails
            return train_and_save_model()
    else:
        return train_and_save_model()

# Initialize model and dictionaries on import
clf, le = load_model()
load_aux_dicts()
if cols is None:
    cols = []
if not symptoms_dict and cols:
    symptoms_dict = {symptom: idx for idx, symptom in enumerate(cols)}

# -------------------------
# Helper functions
# -------------------------
def normalize_symptom(s):
    return s.strip().replace(" ", "_").lower()

def calc_severity(symptoms, days):
    # severityDictionary keys are normalized already
    score = sum(severityDictionary.get(normalize_symptom(symptom), 0) for symptom in symptoms)
    # weight by days, small smoothing
    severity_score = (score * max(1, days)) / (len(symptoms) + 1)
    if severity_score < 5:
        return "low"
    if severity_score < 10:
        return "moderate"
    if severity_score < 15:
        return "high"
    return "critical"

def sec_predict(symptoms):
    """
    Return top-K (disease, confidence_percent) sorted by confidence desc.
    """
    input_vector = np.zeros(len(symptoms_dict))
    for symptom in symptoms:
        key = normalize_symptom(symptom)
        if key in symptoms_dict:
            input_vector[symptoms_dict[key]] = 1
    if hasattr(clf, "predict_proba"):
        probs = clf.predict_proba([input_vector])[0]
    else:
        # fallback: use decision function or 1-hot from predict
        pred = clf.predict([input_vector])[0]
        probs = np.zeros(len(le.classes_))
        probs[pred] = 1.0
    top_indices = probs.argsort()[-TOP_K:][::-1]
    results = []
    for i in top_indices:
        disease = le.inverse_transform([i])[0]
        results.append((disease, round(float(probs[i]) * 100.0, 2)))
    return results

def dynamic_followups(matched_symptoms, top_candidates):
    """
    For the top candidate diseases, find symptoms that best discriminate between them,
    preferring symptoms that are not already matched by the user.
    Approach:
      - From training_df, compute symptom prevalence (mean) per disease for each symptom column.
      - For top candidates, compute absolute difference in prevalence between top1 and others,
        score each symptom by sum of abs diffs, pick top symptoms not already matched.
    """
    candidates = [c[0] for c in top_candidates]
    if training_df is None or training_df.empty:
        # fallback to static common followups
        return ["nausea", "vomiting", "blurred_vision"][:FOLLOWUP_COUNT]

    # Ensure columns are normalized keys
    symptom_cols = [c for c in training_df.columns[:-1]]

    # compute prevalence per disease
    prevalence = {}
    for disease in candidates:
        subset = training_df[training_df["prognosis"] == disease]
        if subset.empty:
            prevalence[disease] = np.zeros(len(symptom_cols))
        else:
            prevalence[disease] = subset[symptom_cols].mean(axis=0).values  # between 0 and 1

    # compute discriminative score (sum of abs diffs from first candidate)
    top1 = candidates[0]
    diffs = np.zeros(len(symptom_cols))
    for disease in candidates[1:]:
        diffs += np.abs(prevalence[top1] - prevalence[disease])

    # create list of (symptom, score)
    symptom_scores = list(zip(symptom_cols, diffs))
    symptom_scores.sort(key=lambda x: x[1], reverse=True)

    # pick symptoms not already matched
    matched_normalized = set(normalize_symptom(s) for s in matched_symptoms)
    followups = []
    for symptom, score in symptom_scores:
        key = normalize_symptom(symptom)
        if key in matched_normalized:
            continue
        # only consider if score reasonably high
        if score <= 0:
            continue
        followups.append(key)
        if len(followups) >= FOLLOWUP_COUNT:
            break

    # fallback if none found
    if not followups:
        fallbacks = ["nausea", "vomiting", "fatigue"]
        return [s for s in fallbacks if s not in matched_normalized][:FOLLOWUP_COUNT]

    return followups

# -------------------------
# Public API
# -------------------------
def predict_disease(symptoms, days=1):
    """
    Input:
      symptoms: list of symptom strings (free-text)
      days: integer (days since onset)
    Output:
      dict with either:
        - error
      or
        - follow_up: [symptom1, symptom2, ...], predictions: [(disease, confidence),...]
      or
        - disease, confidence, description, precautions, severity, matched_symptoms, predictions
    """
    if not isinstance(symptoms, (list, tuple)):
        return {"error": "Symptoms must be a list of strings."}
    if len(symptoms) == 0:
        return {"error": "No symptoms provided."}

    normalized = [normalize_symptom(s) for s in symptoms if s and str(s).strip()]
    matched = [s for s in normalized if s in symptoms_dict]
    if not matched:
        # try partial matches (very small fuzzy fallback)
        # attempt exact match on keys ignoring underscores
        keys = set(symptoms_dict.keys())
        for s in normalized:
            alt = s.replace("_", "")
            for k in keys:
                if k.replace("_", "") == alt:
                    matched.append(k)
                    break

    if not matched:
        return {"error": "No valid symptoms found in our symptom list."}

    predictions = sec_predict(matched)
    top_disease, top_conf = predictions[0]

    # If low confidence, ask follow-up questions
    if top_conf < CONFIDENCE_THRESHOLD:
        follow_up_symptoms = dynamic_followups(matched, predictions)
        return {"follow_up": follow_up_symptoms, "predictions": predictions}

    # high enough confidence -> return full result
    description = description_list.get(top_disease, "No description available.")
    precautions = precautionDictionary.get(top_disease, ["No precautions found."])
    severity = calc_severity(matched, days)

    return {
        "disease": top_disease,
        "confidence": top_conf,
        "description": description,
        "precautions": precautions,
        "severity": severity,
        "matched_symptoms": matched,
        "predictions": predictions
    }

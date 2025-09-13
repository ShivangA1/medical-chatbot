import pandas as pd

# Load training data
df = pd.read_csv("Data/Training.csv")

# Extract unique diseases
known_diseases = df["prognosis"].unique()

# Print them
print("🧠 Diseases currently recognized by the AI:\n")
for i, disease in enumerate(sorted(known_diseases), 1):
    print(f"{i}) {disease}")
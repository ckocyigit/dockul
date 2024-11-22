# Wähle das Basis-Image
FROM python:3.13-slim

# Setze das Arbeitsverzeichnis
WORKDIR /app

# Kopiere die Anforderungen in das Container-Dateisystem
COPY requirements.txt .

# Installiere die Python-Abhängigkeiten
RUN pip install --no-cache-dir -r requirements.txt

# Kopiere den Anwendungscode in das Container-Dateisystem
COPY . .

# Führe das Skript beim Start des Containers aus
CMD ["python", "app.py"]

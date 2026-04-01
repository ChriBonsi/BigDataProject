#!/bin/bash

echo "Inizializzazione database con dati di test..."
python seed_db.py

echo "Database pronto. In attesa di Ofelia..."

tail -f /dev/null
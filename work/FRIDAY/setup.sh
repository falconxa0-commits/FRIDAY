#!/bin/bash

echo "--- Project FRIDAY One-Command Setup ---"

if [ ! -f .env ]; then
    cp .env.example .env
    echo ".env created from .env.example"
fi

# Prompt for minimal required keys
read -p "Enter your ANTHROPIC_API_KEY: " anthropic_key
if [ ! -z "$anthropic_key" ]; then
    sed -i "s/ANTHROPIC_API_KEY=.*/ANTHROPIC_API_KEY=$anthropic_key/" .env
fi

read -p "Enter your FRIDAY_API_TOKEN (for security): " friday_token
if [ ! -z "$friday_token" ]; then
    sed -i "s/FRIDAY_API_TOKEN=.*/FRIDAY_API_TOKEN=$friday_token/" .env
fi

echo "Initializing Workspace..."
mkdir -p friday_workspace

echo "Starting Docker stack..."
docker-compose up --build -d

echo "FRIDAY is coming online. Access the API at http://localhost:8000"

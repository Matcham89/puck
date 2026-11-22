#!/bin/sh
# Remove the cluster. Keeps the registry container and your secrets.
kind delete cluster --name event-driven-ai

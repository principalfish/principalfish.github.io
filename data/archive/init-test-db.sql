-- Create the test database with PostGIS extension
CREATE DATABASE election_maps_test;
\c election_maps_test
CREATE EXTENSION IF NOT EXISTS postgis;

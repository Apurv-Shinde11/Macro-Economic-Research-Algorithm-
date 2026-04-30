import requests

url = "https://nurqjuywtypqvciyjlrw.supabase.co/rest/v1/profiles?select=id,email,tier"
headers = {
    "apikey": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im51cnFqdXl3dHlwcXZjaXlqbHJ3Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3Njc4Njg5MywiZXhwIjoyMDkyMzYyODkzfQ.6Jb1NN4pXK2jl6FGLOnAX1wk1tJ4l7EOfSuTPEH87eI",
    "Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im51cnFqdXl3dHlwcXZjaXlqbHJ3Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3Njc4Njg5MywiZXhwIjoyMDkyMzYyODkzfQ.6Jb1NN4pXK2jl6FGLOnAX1wk1tJ4l7EOfSuTPEH87eI"
}
r = requests.get(url, headers=headers)
print(r.status_code)
print(r.json())
import pickle

path = "/home/alex/projects/ai/models/metadata.pkl"

with open(path, "rb") as f:
    metadata = pickle.load(f)

print(type(metadata))

if isinstance(metadata, dict):
    print("Ключи:")
    for key in metadata.keys():
        print("-", key)

    print("\nПолное содержимое:")
    print(metadata)
import json, csv

seat_dict = {
    "North East Hertfordshire" : "Hertfordshire North East",
    "Bury St. Edmunds" : "Bury St Edmunds",
    "South Leicestershire" : "Leicestershire South",
    "Ashton-under-Lyne" : "Ashton Under Lyne",
    "Lewisham, Deptford" : "Lewisham Deptford",
    "East Devon" : "Devon East",
    "Harwich and North Essex": "Harwich and Essex North",
    "Hereford and South Herefordshire" : "Hereford and Herefordshire Sou",
    "City of Durham" : "Durham, City of",
    "Normanton, Pontefract and Castleford" : "Normanton, Pontefract and Cast",
    "North Shropshire" : "Shropshire North",
    "North West Hampshire" : "Hampshire North West",
    "South East Cornwall" : "Cornwall South East",
    "North Norfolk": "Norfolk North",
    "The Cotswolds" : "Cotswolds, The",
    "Mid Sussex" : "Sussex Mid",
    "Kingston upon Hull North" : "Hull North",
    "South Swindon" : "Swindon South",
    "Mid Derbyshire" : "Derbyshire Mid",
    "Sheffield Brightside and Hillsborough"  :  "Sheffield Brightside and Hills",
    "Liverpool, West Derby" : "Liverpool West Derby",
    "West Lancashire" : "Lancashire West",
    "South Cambridgeshire" : "Cambridgeshire South",
    "South Staffordshire" : "Staffordshire South",
    "Liverpool, Walton" : "Liverpool Walton",
    "Middlesbrough South and East Cleveland" : "Middlesbrough South and Clevel",
    "South West Surrey" : "Surrey South West",
    "Ealing, Southall" : "Ealing Southall",
    "North Durham" : "Durham North",
    "North West Durham" : "Durham North West",
    "Mid Worcestershire" : "Worcestershire Mid",
    "St. Ives" : "St Ives",
    "North Somerset" : "Somerset North",
    "Bridgwater and West Somerset" : "Bridgwater and Somerset West",
    "North Herefordshire" : "Herefordshire North",
    "West Suffolk" : "Suffolk West",
    "South Northamptonshire" : "Northamptonshire South",
    "North East Derbyshire" : "Derbyshire North East",
    "North Swindon" : "Swindon North",
    "South Basildon and East Thurrock" : "Basildon South and Thurrock East",
    "South Thanet" : "Thanet South",
    "The Wrekin" : "Wrekin, The",
    "Plymouth, Sutton and Devonport" : "Plymouth Sutton and Devonport",
    "Mid Norfolk" : "Norfolk Mid",
    "Central Devon" : "Devon Central",
    "North East Cambridgeshire" : "Cambridgeshire North East",
    "North East Somerset" : "Somerset North East",
    "North Warwickshire" : "Warwickshire North",
    "Manchester, Gorton" : "Manchester Gorton",
    "Mid Dorset and North Poole" : "Dorset Mid and Poole North",
    "North Dorset" : "Dorset North",
    "Enfield, Southgate" :"Enfield Southgate",
    "St. Austell and Newquay": "St Austell and Newquay",
    "North Thanet" : "Thanet North",
    "East Hampshire" : "Hampshire East",
    "Kingston upon Hull West and Hessle" : "Hull West and Hessle",
    "East Yorkshire" : "Yorkshire East",
    "St. Albans" : "St Albans",
    "Kingston upon Hull East" : "Hull East",
    "South Derbyshire" : "Derbyshire South",
    "North Wiltshire" : "Wiltshire North",
    "North Devon" : "Devon North",
    "St. Helens North" : "St Helens North",
    "South West Norfolk" : "Norfolk South West",
    "North Cornwall" : "Cornwall North",
    "Mid Bedfordshire" : "Bedfordshire Mid",
    "North West Cambridgeshire" : "Cambridgeshire North West",
    "Liverpool, Wavertree" : "Liverpool Wavertree",
    "Hackney North and Stoke Newington" : "Hackney North and Stoke Newing",
    "East Worthing and Shoreham" : "Worthing East and Shoreham",
    "South East Cambridgeshire" : "Cambridgeshire South East",
    "Faversham and Mid Kent" : "Faversham and Kent Mid",
    "Cities of London and Westminster" : "Cities of London and Westminst",
    "Manchester, Withington" : "Manchester Withington",
    "Central Suffolk and North Ipswich" : "Suffolk Central and Ipswich No",
    "Uxbridge and South Ruislip" : "Uxbridge and Ruislip South",
    "West Worcestershire" : "Worcestershire West",
    "South West Wiltshire" : "Wiltshire South West",
    "South Dorset" : "Dorset South",
    "Holborn and St. Pancras" : "Holborn and St Pancras",
    "Liverpool, Riverside" : "Liverpool Riverside",
    "South Suffolk" : "Suffolk South",
    "Torridge and West Devon" : "Devon West and Torridge",
    "West Dorset" : "Dorset West",
    "North West Leicestershire" : "Leicestershire North West",
    "South West Devon": "Devon South West",
    "North West Norfolk" : "Norfolk North West",
    "St. Helens South and Whiston" : "St Helens South and Whiston",
    "North Tyneside"  : "Tyneside North",
    "North East Hampshire" : "Hampshire North East",
    "East Surrey" : "Surrey East",
    "South West Hertfordshire" : "Hertfordshire South West",
    "South Norfolk" : "Norfolk South",
    "City of Chester": "Chester, City of"

}

party_map = {
    "Labour" : "labour",
    "Conservative" : "conservative",
    "Lib-Dem" : "libdems",

}


task_info = {}
old_info = {}

task_seat_names = {}

with open("Task.csv", "r") as input_csv:
    reader = csv.DictReader(input_csv, delimiter=",")
    for row in reader:
        task_info[row["seat"]] = row


json_data = open("oldinfo.json").read()
mapdata = json.loads(json_data)

for item in mapdata:
    if mapdata[item]["seat_info"]["area"] not in ["scotland", "wales", "northernireland"]:
        old_info[item] = mapdata[item]

combined_info = {}
for item in old_info:
    combined_info[item] = old_info[item]
    try:
        combined_info[item]["new_data"] = task_info[item]
    except KeyError:
        combined_info[item]["new_data"] = task_info[seat_dict[item]]



with open("info.json", "w") as output:
    json.dump(combined_info, output)

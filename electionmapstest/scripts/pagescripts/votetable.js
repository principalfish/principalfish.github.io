function displayVoteTotals(data) {

		$("#totalstable").remove();

		var table_to_add = "<table id=\"totalstable\" class=\"tablesorter\"><thead><tr><th>Party</th>" +
												"<th class=\"tablesorter-header\">Seats</th>";


		simpleTables = ["2010parliament", "2016parliament"];

		if (simpleTables.indexOf(pageSetting) >= 0 ){


			table_to_add += "<th class=\"tablesorter-header\">Votes</th><th class=\"tablesorter-header\">Vote %</th></tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
			"</tfoot></table>";

		}


		else {

			table_to_add += "<th class=\"tablesorter-header\">Change</th>" +
			"<th class=\"tablesorter-header\">Votes</th><th class=\"tablesorter-header\">Vote %</th>" +
			"	<th class=\"tablesorter-header\">% +/-</th></tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
			"</tfoot></table>";

		}

		$("#totalstablediv").append(table_to_add);


		var percentChange;
		var plussign1, plussign2;

		$.each(data, function(i){

				if (data[i].change > 0){

					plussign1 = "+";
				}

				else{

					plussign1 = "";

				}

				if (data[i].percentchange > 0){
					plussign2 = "+";
				}
				else {
					plussign2 = "";
				}

			if (data[i].percentchange == undefined){

			 	percentChange = "";

			}

			else {

				percentChange = data[i].percentchange.toFixed(2);

			}

			if (i == data.length -1){

				var table_foot = "<tr style=\"text-align: center;\" class=\"" + data[i].code +"\"><td style=\"text-align: left;\">"
				+ partylist[data[i].code] + "</td><td style=\"text-align: right;\">"
					+ data[i].seats + "</td>";

				if (simpleTables.indexOf(pageSetting) >= 0 ){

					table_foot += "<td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td></td></tr>";

				}

				else {

					table_foot += "<td></td><td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td></td><td></td></tr>";

				}

				$("#totalstablefoot").append(table_foot);
				}

			else if (data[i].votes > 0){

				var table_row = "<tr><td><div class=\"party-flair " + data[i].code + "\"></div>"
				+ partylist[data[i].code] + "</td><td style=\"text-align: right;\">"
				+ data[i].seats + "</td>";

				if (simpleTables.indexOf(pageSetting) >= 0 ){

					table_row += "<td style=\"text-align: right;\">" + data[i].votes.toLocaleString() + "</td><td style=\"text-align: center;\">"
					+ data[i].votepercent.toFixed(2) + "</td></tr>";

				}


				else {

					table_row += "<td style=\"text-align: right;\">"
					+ plussign1 + data[i].change + "</td><td style=\"text-align: right;\">"
					+ data[i].votes.toLocaleString() + "</td><td style=\"text-align: center;\">"
					+ data[i].votepercent.toFixed(2) + "</td><td>"
					+ plussign2 + percentChange + "</td></tr>";

				}

				$("#totalstableinfo").append(table_row);
			}

		});

		$("#totalstable").tablesorter({

				sortInitialOrder: "desc",
	      headers: {
					0: {
						sorter: false
					}
	    },
				sortList:[[3,1]]
		});
};


nationalVoteTotals = [];
greatbritainVoteTotals = [];
englandVoteTotals = [];
scotlandVoteTotals = [];
walesVoteTotals = [];
northernirelandVoteTotals = [];
northeastenglandVoteTotals = [];
northwestenglandVoteTotals = [];
westmidlandsVoteTotals = [];
eastmidlandsVoteTotals = [];
yorkshireandthehumberVoteTotals = [];
eastofenglandVoteTotals = [];
southeastenglandVoteTotals = [];
southwestenglandVoteTotals = [];
londonVoteTotals = [];

//user eslect region vote totals
function selectAreaInfo(value){
	if (value == "country") {displayVoteTotals(nationalVoteTotals)};
  if (value == "null") {displayVoteTotals(nationalVoteTotals)};
	if (value == "england") {displayVoteTotals(englandVoteTotals)};
	if (value == "scotland") {displayVoteTotals(scotlandVoteTotals)};
	if (value == "eastofengland") {displayVoteTotals(eastofenglandVoteTotals)};
	if (value == "northeastengland") {displayVoteTotals(northeastenglandVoteTotals)};
	if (value == "northwestengland") {displayVoteTotals(northwestenglandVoteTotals)};
	if (value == "southwestengland") {displayVoteTotals(southwestenglandVoteTotals)};
	if (value == "southeastengland") {displayVoteTotals(southeastenglandVoteTotals)};
	if (value == "london") {displayVoteTotals(londonVoteTotals)};
	if (value == "wales") {displayVoteTotals(walesVoteTotals)};
	if (value == "northernireland") {displayVoteTotals(northernirelandVoteTotals)};
	if (value == "yorkshireandthehumber") {displayVoteTotals(yorkshireandthehumberVoteTotals)};
	if (value == "eastmidlands") {displayVoteTotals(eastmidlandsVoteTotals)};
	if (value == "westmidlands") {displayVoteTotals(westmidlandsVoteTotals)};
	if (value == "greatbritain") {displayVoteTotals(greatbritainVoteTotals)};
}

function getVoteTotals(area){



    var areas = [];

    if (area == "all"){

      areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]).concat(regions["northernireland"]);

    }

    else if (area == "greatbritain"){

      areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]);

    }

    else if (area == "england"){

      areas = regions["england"];

    }

    else {

      areas.push(area);

    }

		var totalvotescast = 0;

		var oldturnout = 0;

		$.each(seatData, function(seat){

			if (areas.indexOf(seatData[seat]["seat_info"]["area"]) != -1 ){

				totalvotescast += parseInt(seatData[seat]["seat_info"]["turnout"]);

			}
		});	

		$.each(areas, function(area){

			year = String(parseInt(pageSetting.slice(0,4)));

			yearRelative = {
				"2020" : "2015",
				"2016" : "2015",
				"2015" : "2010",
				"2010" : "2005"
			};

			year = yearRelative[year]


			oldturnout +=  previousTotalsByYearByParty[year][areas[area]]["turnout"];


		})



		var holdingArray = [];
		var totalseats = 0;
		var totalvotes = 0;

		var otherSeatssum = 0;
		var otherChange = 0;
		var otherTotalVotes = 0;
		var otherVotePercent = 0;

		$.each(parties, function(party){

			info = {};
			var code = parties[party];

			if (code == "other" || code == "others"){

				code = "other"

			}

			var seatssum = 0;
			var change = 0;
			var totalvotes = 0;
			var votepercent = 0;
			var oldvotetotal = 0;
			var old_vote_percent;

			$.each(seatData, function(seat){

				if (areas.indexOf(seatData[seat]["seat_info"]["area"]) > -1){

					if (seatData[seat]["seat_info"]["winning_party"] == code){

						seatssum += 1;
						totalseats += 1;

					}


					if (seatData[seat]["seat_info"]["incumbent"] == code){

						change += 1;

					}

					parties_in_seat = Object.keys(seatData[seat]["party_info"]);

					if (parties_in_seat.indexOf(parties[party]) != -1) {

						totalvotes += seatData[seat]["party_info"][parties[party]]["total"];
						oldvotetotal += seatData[seat]["party_info"][parties[party]]["old"];

					}

					}
				});

			votepercent =  parseFloat((100 * totalvotes / parseFloat(totalvotescast)).toFixed(2));
			old_vote_percent =  parseFloat((100 * oldvotetotal / parseFloat(oldturnout)).toFixed(2));
			var vote_percent_change = votepercent - old_vote_percent;

			change = seatssum - change;

			if (code == "other"){

				otherSeatssum = seatssum;
				otherChange = change;
				otherTotalVotes +=totalvotes;
				otherVotePercent += votepercent;

			}

			else {

				info["code"] = code;
				info["seats"] = seatssum;
				info["change"] = change;
				info["votes"] = totalvotes;
				info["votepercent"] = votepercent;
				info["percentchange"] = vote_percent_change;
				holdingArray.push(info);

			}
		});

		var others = {"code": "other", "seats" : otherSeatssum, "change": otherChange, "votes": otherTotalVotes, "votepercent" : otherVotePercent};
		var totals = {"code": "total", "seats" : totalseats - otherSeatssum, "change": "", "votes": totalvotescast, "votepercent" : " "};

		holdingArray.push(others);
    holdingArray.push(totals);


    alterTable(area, holdingArray);
}


function alterTable(area, holdingarray){

  if (area == "all"){
    nationalVoteTotals = holdingarray;
	}

  if (area == "greatbritain"){
    greatbritainVoteTotals = holdingarray;
  }

  if (area == "england"){
    englandVoteTotals = holdingarray;
  }

  if (area == "scotland"){
    scotlandVoteTotals = holdingarray;
  }

  if (area == "wales"){
    walesVoteTotals = holdingarray;
  }

  if (area == "northernireland"){
    northernirelandVoteTotals = holdingarray;
  }

  if (area == "northeastengland"){
    northeastenglandVoteTotals = holdingarray;
  }

  if (area == "northwestengland"){
    northwestenglandVoteTotals = holdingarray;
  }

  if (area == "westmidlands"){
    westmidlandsVoteTotals = holdingarray;
  }

  if (area == "eastmidlands"){
    eastmidlandsVoteTotals = holdingarray;
  }

  if (area == "yorkshireandthehumber"){
    yorkshireandthehumberVoteTotals = holdingarray;
  }

  if (area == "eastofengland"){
    eastofenglandVoteTotals = holdingarray;
  }

  if (area == "southeastengland"){
    southeastenglandVoteTotals = holdingarray;
  }

  if (area == "southwestengland"){
    southwestenglandVoteTotals = holdingarray;
  }

  if (area == "london"){
    londonVoteTotals = holdingarray;
  }
}

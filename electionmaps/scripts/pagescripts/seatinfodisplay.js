// fills out table of info at top of #right
var oldPartyClass = null;
var oldIncumbentClass = null;

var partyNameElement = "#information-party .party-name";
var partyFlairElement = "#information-party .party-flair";
var gainNameElement = "#information-gain .party-name";
var gainFlairElement = "#information-gain .party-flair";

function seatinfo(d){

	var seatInfo = seatData[d.properties.name]["seat_info"];

	$(partyFlairElement).removeClass(oldPartyClass);
  $(gainFlairElement).removeClass(oldIncumbentClass);
	$("#information-seatname-span").text(d.properties.name);
	$("#information-region").text("Region: " + regionlist[seatInfo["area"]]);

	$(partyNameElement).text("Party: " + partylist[seatInfo["winning_party"]]);
    $(partyFlairElement).addClass(seatInfo["winning_party"]);

	if (seatInfo["winning_party"] != seatInfo["incumbent"]) {

		$(gainNameElement).text("Gain from " + partylist[seatInfo["incumbent"]]);
        $(gainFlairElement).addClass(seatInfo["incumbent"]);

    }

	else {

		$(gainNameElement).text("");

    }

	$("#information-majority").text("Majority: " + seatInfo["maj_percent"]
	+ " % = " + seatInfo["maj"]);

	var seatTurnout = (100 * seatInfo["turnout"] / seatInfo["electorate"]).toFixed(2) + "%";

	$("#information-turnout").text("Turnout : " + seatTurnout );

	$("#information-pie").html(piechart(d));

	oldPartyClass = seatInfo["winning_party"];
    oldIncumbentClass = seatInfo["incumbent"];
}

function piechart(d){

	$("#information-pie").empty();
	$("#information-chart").empty();
	$("#information-chart").html("<table></table>");

	var data = [];
	var relevant_party_info = seatData[d.properties.name]["party_info"];

	$.each(relevant_party_info, function(d){

		votes = relevant_party_info[d]["percent"];

		if (votes > 0){

			data.push({party: d, votes: votes, vote_change: relevant_party_info[d]["change"]});

		}

	})

	var filterdata = [];

	$.each(data, function(i){

		if (data[i].votes > 0 && data[i].party != "others"){

			filterdata.push(data[i]);
		}

	});

	filterdata.sort(function(a, b){

			return b.votes - a.votes;

	});

	var keys = Object.keys(relevant_party_info);

	if (keys.indexOf("others") != -1 && relevant_party_info["others"]["percent"] > 0){

		filterdata.push({party: "others", votes: relevant_party_info["others"]["percent"], vote_change: relevant_party_info["others"]["change"]});

	}

	var barchartdata = [];

	$.each(filterdata, function(i){

		if (filterdata[i].votes >= 3){

			barchartdata.push(filterdata[i]);

		}

	});

	var dataitems = barchartdata.length;
	var margin = {top: 10, right: 0, bottom: 10, left: 25};

	var width = 250 - margin.left - margin.right;
	var height = 225 - margin.top - margin.bottom;
	var bargap = 2;
	var barwidth = d3.min([60, (width / dataitems) - bargap]);
	var animationdelay = 250;

	var svg1 = d3.select("#information-pie")
		.append("svg")
			.attr("width", width + margin.left + margin.right)
			.attr("height", height + margin.top + margin.bottom)
		.append("g")
    	.attr("transform", "translate(" + margin.left + "," + margin.top + ")");

	var x = d3.scale.ordinal().range([0, dataitems * (barwidth + 2)]);

	var xAxis = d3.svg.axis()
	    .scale(x)
	    .orient("bottom");

	var y = d3.scale.linear()
		.range([height, 0]);

	var yAxis = d3.svg.axis()
    .scale(y)
    .orient("left")
    .ticks(6);

	var max_of_votes = d3.max(barchartdata, function(d) { return d.votes; });
	y.domain([0, d3.max([60, (max_of_votes + (10 - max_of_votes % 10))])]);

	svg1.append("g")
      .attr("class", "x axis")
      .attr("transform", "translate(0," + height + ")")
      .call(xAxis);

	svg1.append("g")
    .attr("class", "y axis")
    .call(yAxis);

	svg1.selectAll("rect")
		.data(barchartdata)
		.enter()
		.append("rect")
      .attr("x", function(d, i) { return bargap * (i + 1) + barwidth * (i); })
      .attr("width", barwidth)
			.attr("y", height)
      .attr("height", 0)
			.attr("class", function(d) { return d.party;})
		.transition()
      .delay(function(d, i) { return i * animationdelay / 2; })
      .duration(animationdelay)
      .attr("y", function(d) { return y(d.votes); })
      .attr("height", function(d) { return height - y(d.votes); });


	$.each(filterdata, function(i){

		var plussign = "";
		if (filterdata[i].vote_change > 0){
			plussign = "+";

		}

		var vote_change;

		if (filterdata[i].vote_change ==""){

			vote_change = "";

		}

		else {

			vote_change = parseFloat(filterdata[i].vote_change).toFixed(2);

		}

		var to_add = "<tr><td><div class= \" party-flair " + filterdata[i].party + "\"></div><td style=\"max-width: 170px;\">" +
			seatData[d.properties.name]["party_info"][filterdata[i].party]["name"] + "</td><td>" + (parseFloat(filterdata[i].votes)).toFixed(2) +  "%</td>";

		if (pageSetting == "2010parliament"){

			to_add += "</tr>";

		}

		else {

			to_add += ("<td>" + plussign + vote_change +  "</td></tr>");

		}

		$("#information-chart table").append(to_add);

	});

	if (seatData[d.properties.name]["seat_info"]["byelection"] != null){

		$("#information-byelection").text("By-election since " + pageSetting.slice(0, 4));

	}

	else {

		$("#information-byelection").text("");

	}

}

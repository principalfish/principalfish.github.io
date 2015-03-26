//add to d3js prototype
d3.selection.prototype.moveToFront = function() {
							return this.each(function(){
							this.parentNode.appendChild(this);
						});
					};



// far left filter scripts
var filterStates = [{party: "null"}, {gain:"null"}, {region: "null"}, {majoritylow : 0}, {majorityhigh : 100}]

var seatsAfterFilter = [];
var searchSeatData = [];
var seatNames = [];




function filterMap(){

	var	party  = filterStates[0].party;
	var gains = filterStates[1].gain;
	var region = filterStates[2].region;
	var majoritylow = filterStates[3].majoritylow
	var majorityhigh = filterStates[4].majorityhigh


	if (isNaN(majoritylow))
		majoritylow = 0
	if (isNaN(majorityhigh))
		majorityhigh = 100

	seatsAfterFilter = [];

	d3.json("/election2015/data/projection.json", function(uk){

			g.selectAll(".map")
				.attr("id", "filtertime")

			if (party == "null")
				g.selectAll("#filtertime")
					.attr("id", "partyfiltered")
			else
				g.selectAll("#filtertime")
					.attr("style", function(d){
						if (party != seatData[d.properties.name]["party"])
							return "opacity: 0.1";
						})
					.attr("id", function(d){
						if (party == seatData[d.properties.name]["party"])
							return "partyfiltered"
						});



			if (gains == "null")
					g.selectAll("#partyfiltered")
						.attr("id", "gainfiltered")

			else
				if (gains == "gains")
					g.selectAll("#partyfiltered")
						.attr("style", function(d) {
							if (seatData[d.properties.name]["party"] == seatData[d.properties.name]["incumbent"])
								return "opacity: 0.1";
							})
						.attr("id", function(d){
							if (seatData[d.properties.name]["party"] != seatData[d.properties.name]["incumbent"])
								return "gainfiltered"
						});

				if (gains == "nochange")
					g.selectAll("#partyfiltered")
						.attr("style", function(d) {
							if (seatData[d.properties.name]["party"] != seatData[d.properties.name]["incumbent"])
								return "opacity: 0.1";
							})
						.attr("id", function(d){
							if (seatData[d.properties.name]["party"] == seatData[d.properties.name]["incumbent"])
								return "gainfiltered"
						});



			if (region == "null")
				g.selectAll("#gainfiltered")
					.attr("id", "regionfiltered")

			else
				g.selectAll("#gainfiltered")
					.attr("style", function(d){
						if (region != seatData[d.properties.name]["area"])
							return "opacity: 0.1";
					})
					.attr("id", function(d){
						if (region == seatData[d.properties.name]["area"])
							return "regionfiltered"
					});


			g.selectAll("#regionfiltered")
				.attr("style", function(d) {

					if (parseFloat(seatData[d.properties.name]["majority"]) < majoritylow || parseFloat(seatData[d.properties.name]["majority"]) > majorityhigh )
						return "opacity: 0.1"

					})
				.each(function(d) {
					if (parseFloat(seatData[d.properties.name]["majority"]) >= majoritylow && parseFloat(seatData[d.properties.name]["majority"]) <= majorityhigh )
						seatsAfterFilter.push(d);
					});


			g.selectAll(".map")
				.attr("id", function(d) {
					return "i" + seatData[d.properties.name]["id"]
				});
			$("#totalfilteredseats").html(" ");
			$("#filteredlisttable").html(" ");


		});
	}

function resetFilter(){

	filterStates[0].party = "null";
	filterStates[1].gain = "null";
	filterStates[2].region = "null";
	filterStates[3].majoritylow = 0;
	filterStates[4].majorityhigh = 100;

	filterMap();

	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#dropdowngains option:eq(0)").prop("selected", true);
	$("#dropdownregion option:eq(0)").prop("selected", true);
	$("#majority").get(0).reset()

	$("#totalfilteredseats").html(" ");
	$("#filteredlisttable").html(" ");
}

function generateSeatList(){
			$("#totalfilteredseats").html(" ");g
			$("#filteredlisttable").html(" ");

			$("#totalfilteredseats").append("<p>Total : " + seatsAfterFilter.length + "</p>");
			$.each(seatsAfterFilter, function(i){

				if (filterStates[1].gain == "gains" && filterStates[0].party != "null")
				$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["incumbent"] +
				"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>")


				else
					$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["party"] +
					"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>")

			});


		}

function zoomToClickedFilteredSeat(d){
	var id = "#i" + seatData[d.properties.name]["id"];
	//rewrite at some point

	previous = d3.select(previousnode)
	current = d3.select(id);

	repeat();

	function repeat(){
		previous.transition()
			.attr("opacity", 1)

		current
			.transition()
				.duration(1500)
				.attr("opacity", 0.2)
			.transition()
				.duration(1500)
				.attr("opacity", 1)
			.each("end", repeat);
		}




	var bounds = path.bounds(d),
		dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
		dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
		x = (bounds[0][0] + bounds[1][0]) / 2,
		y = (bounds[0][1] + bounds[1][1]) / 2,

		scale = .025 / Math.max(dx /  width,  dy / height),
		translate = [width / 2 - scale * x, height / 2 - scale * y];

	disableZoom();

	svg.transition()
			.duration(1500)
			.call(zoom.translate(translate).scale(scale).event)
			.each("end", enableZoom);

	seatinfo(d);
	previousnode = id;
	}

// scripts for zoom/pan/clicked


function clicked(d) {

	previous = d3.select(previousnode)
	current = d3.select(this);

	repeat();

	function repeat(){
		previous.transition()
			.attr("opacity", 1)

		current
			.transition()
				.duration(1500)
				.attr("opacity", 0.2)
			.transition()
				.duration(1500)
				.attr("opacity", 1)
			.each("end", repeat);
		}

	var bounds = path.bounds(d),
		dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
		dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
		x = (bounds[0][0] + bounds[1][0]) / 2,
		y = (bounds[0][1] + bounds[1][1]) / 2,

		scale = .025 / Math.max(dx /  width,  dy / height),
		translate = [width / 2 - scale * x, height / 2 - scale * y];

	disableZoom();

	svg.transition()
			.duration(1500)
			.call(zoom.translate(translate).scale(scale).event)
			.each("end", enableZoom);

	seatinfo(d);
	previousnode = this;
}

function disableZoom(){
	svg.on("mousedown.zoom", null);
	svg.on("mousemove.zoom", null);
	svg.on("dblclick.zoom", null);
	svg.on("touchstart.zoom", null);
	svg.on("wheel.zoom", null);
	svg.on("mousewheel.zoom", null);
	svg.on("MozMousePixelScroll.zoom", null);
}

function enableZoom(){
	svg.call(zoom);
}

function reset() {
	disableZoom();
	svg.transition()
		.duration(1500)
		.call(zoom.translate([0, 0]).scale(1).event)
		.each("end", enableZoom);

}

function zoomed() {
	g.style("stroke-width", 1.5 / d3.event.scale + "px");
	g.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");

}

function stopped() {
	if (d3.event.defaultPrevented) d3.event.stopPropagation();
}

// seatinfo on right

var oldclass = null;

function seatinfo(d){

	$("#information").removeClass(oldclass);
	$("#information").addClass(seatData[d.properties.name]["party"])
	$("#information-seatname").html("<td>Seat</td><td style=\"width:360px\"> " + d.properties.name +
	"</td><td id=\"rightcolumninfotable\">" + regionlist[seatData[d.properties.name]["area"]] + "</td>");
	$("#information-party").html("<td>Party</td><td>" + partylist[seatData[d.properties.name]["party"]] + "</td>");
	if (seatData[d.properties.name]["party"] != seatData[d.properties.name]["incumbent"])
		$("#information-gain").html("<td>Gain from</td><td><span id=\"information-gain-span\"class=\"" +
		seatData[d.properties.name]["incumbent"] + "\">" + partylist[seatData[d.properties.name]["incumbent"]] + "</span></td>")
	else
		$("#information-gain").html("<td>No change </td>")
	$("#information-pie").html(piechart(d));

	oldclass = seatData[d.properties.name]["party"];
}



function piechart(d){

	$("#information-pie").empty();
	$("#information-chart").empty();

	$("#information-chart").html("<p></p>");

	var data = [];
	data.push({ party: "labour", votes: seatData[d.properties.name]["labour"]});
	data.push({ party: "conservative", votes: seatData[d.properties.name]["conservative"]});
	data.push({ party: "libdems", votes: seatData[d.properties.name]["libdems"]});
	data.push({ party: "ukip", votes: seatData[d.properties.name]["ukip"]});
	data.push({ party: "snp", votes: seatData[d.properties.name]["snp"]});
	data.push({ party: "plaidcymru", votes: seatData[d.properties.name]["plaidcymru"]});
	data.push({ party: "green", votes: seatData[d.properties.name]["green"]});
	data.push({ party: "dup", votes: seatData[d.properties.name]["dup"]});
	data.push({ party: "sdlp", votes: seatData[d.properties.name]["sdlp"]});
	data.push({ party: "uu", votes: seatData[d.properties.name]["uu"]});
	data.push({ party: "sinnfein", votes: seatData[d.properties.name]["sinnfein"]});
	data.push({ party: "alliance", votes: seatData[d.properties.name]["alliance"]});



	var filterdata = [];


	$.each(data, function(i){

		if (data[i].votes > 0 && data[i].party != "other")
			filterdata.push(data[i]);
	});



	filterdata.sort(function(a, b){
			return b.votes - a.votes ;
	});

	if (seatData[d.properties.name]["other"] > 0)
		filterdata.push({party: "other", votes: seatData[d.properties.name]["other"]})

	var width = 250,
		height = 250;
		radius = Math.min(width, height) / 2;


	var arc = d3.svg.arc()
		.outerRadius(radius - 10)
		.innerRadius(0);

	var pie = d3.layout.pie()
		.sort(null)
		.value(function (d) {
			return d.votes;
		});

	var svg = d3.select("#information-pie").append("svg")
		.attr("width", width)
		.attr("height", height)
		.append("g")
		.attr("transform", "translate(" + width / 2 + "," + height / 2 + ")");

		var g = svg.selectAll(".arc")
			.data(pie(filterdata))
			.enter().append("g")
			.attr("class", "arc");

		g.append("path")
			.attr("d", arc)
			.attr("class", function(d, i){
					return filterdata[i].party;
				});

	// creates table with vote counts/percentages
	$.each(filterdata, function(i){
		$("#information-chart").append("<tr class=" + filterdata[i].party + " style=\"font-weight: bold;\"><td>" +
			partylist[filterdata[i].party] + "</td><td>" + (parseFloat(filterdata[i].votes)).toFixed(2) +  "%</td></tr>")
	})


}



// bottom of right - vote chart


$( function() {
    $.tablesorter.addParser({
        id: "numberWithComma",
        is: function(s) {
            return /^[0-9]?[0-9,\.]*$/.test(s);
        },
        format: function(s) {
            return $.tablesorter.formatFloat(s.replace(/,/g, ''));
        },
        type: "numeric"
    });
});


function displayVoteTotals(data) {
		$("#totalstable").remove()
		$("#totals").append("<table id=\"totalstable\" class=\"tablesorter\"><thead><tr><th>Party</th>" +
												"<th class=\"tablesorter-header\">Seats</th><th class=\"tablesorter-header\">Change</th>" +
												"<th class=\"tablesorter-header\">Vote %</th><th class=\"tablesorter-header\">+/-</th>" +
												"</tr></thead><tbody id=\"totalstableinfo\"></tbody><tfoot id=\"totalstablefoot\">" +
												"</tfoot></table>")

		//find wa yto clear cache of tablesorter properly? -

		var plussign1, plussign2;

		$.each(data, function(i){


				if (data[i].change > 0)
					plussign1 = "+";
				else
					plussign1 = "";

				if (data[i].votepercentchange > 0)
					plussign2 = "+";
				else
					plussign2 = "";

			if (i == data.length -1)
					null

			else if (i == data.length -2)

				$("#totalstablefoot").append("<tr class=\"" + data[i].code +"\"><td>" + partylist[data[i].code] + "</td><td>"
					+ data[i].seats + "</td><td>" + data[i].change + "</td><td>" + (data[i].votepercent).toFixed(2) +
					"</td><td>" + (data[i].votepercentchange) + "</td></tr>");
			else
				if (data[i].votepercent > 0)
					$("#totalstableinfo").append("<tr class=\"" + data[i].code +"\"><td>" + partylist[data[i].code] + "</td><td>"
						+ data[i].seats + "</td><td>"  + plussign1 + data[i].change + "</td><td>" + (data[i].votepercent).toFixed(2) +
						"</td><td>"  + plussign2 + (data[i].votepercentchange).toFixed(2) + "</td></tr>");
		})



		$("#totalstable").tablesorter({

				sortInitialOrder: "asc",
	      headers: {
					1: { sortInitialOrder: 'desc' },
					2: { sortInitialOrder: 'desc' },
	        4: { sortInitialOrder: 'desc' },
					0: {
						sorter: false
					}

	    },

				sortList:[[3,1]]


		});
};


function loadmap(){


	d3.json("/election2015/data/projection.json", function(error, uk) {
		if (error) return console.error(error);



		g.selectAll(".map")
			.data(topojson.feature(uk, uk.objects.projection).features)
			.enter().append("path")
			.attr("class", function(d) {
				return "map " + seatData[d.properties.name]["party"];	})
			.attr("opacity", 1)
			.attr("id", function(d) { return "i" + seatData[d.properties.name]["id"]})
			.attr("d", path)
			.on("click", clicked)
			.append("svg:title")
				.text(function(d) { return seatData[d.properties.name]["seat"]})
			.each(function(d){
				seatsAfterFilter.push(d)
				searchSeatData.push(d)
				seatNames.push(d.properties.name);

			});


		g.append("path")
			.datum(topojson.mesh(uk, uk.objects.projection, function(a, b){
				return seatData[a.properties.name]["area"] != seatData[b.properties.name]["area"] && a != b; }))
			.attr("d", path)
			.attr("class", "boundaries");

	});
}

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


//for use purely in user input calculations
previousTotals = {};
previousPercentages = {};

function getVoteTotalsInitial(data, region) {

	region = region.slice(27)

	$.each(data, function(i){
		var info = {};
		info["code"] = data[i].code
		info["seats"] = data[i].seats
		info["change"] = data[i].change
		info["votepercent"] = data[i].votepercent
		info["votepercentchange"] = data[i].votepercentchange
		nationalVoteTotals.push(info);
	});

	displayVoteTotals(nationalVoteTotals)
}

function getVoteTotals(data, region) {

	if (region == "/election2015/data/previoustotals.csv"){
		$.each(data, function(i){
			var info = {};
			info["region"] = data[i].region;
			info["turnout2010"] = data[i].turnout2010;
			info["conservative"] = data[i].conservative;
			info["labour"] = data[i].labour;
			info["libdems"] = data[i].libdems;
			info["ukip"] = data[i].ukip;
			info["snp"] = data[i].snp;
			info["plaidcymru"] = data[i].plaidcymru;
			info["green"] = data[i].green;
			info["uu"] = data[i].uu;
			info["sdlp"] = data[i].sdlp;
			info["dup"] = data[i].dup;
			info["sinnfein"] = data[i].sinnfein;
			info["alliance"] = data[i].alliance;
			info["other"] = data[i].other;


			previousTotals[data[i].region] = info;
		});

		$.each(data, function(i){
			var info = {};

			info["conservative"] = 100 *  data[i].conservative / parseFloat(data[i].turnout2010);
			info["labour"] = 100 *  data[i].labour / parseFloat(data[i].turnout2010);
			info["libdems"] = 100 *  data[i].libdems / parseFloat(data[i].turnout2010);
			info["ukip"] = 100 *  data[i].ukip / parseFloat(data[i].turnout2010);
			info["snp"] = 100 *  data[i].snp / parseFloat(data[i].turnout2010);
			info["plaidcymru"] = 100 *  data[i].plaidcymru / parseFloat(data[i].turnout2010);
			info["green"] = 100 *  data[i].green / parseFloat(data[i].turnout2010);
			info["uu"] = 100 *  data[i].uu / parseFloat(data[i].turnout2010);
			info["sdlp"] = 100 *  data[i].sdlp / parseFloat(data[i].turnout2010);
			info["dup"] = 100 *  data[i].dup / parseFloat(data[i].turnout2010);
			info["sinnfein"] = 100 *  data[i].sinnfein / parseFloat(data[i].turnout2010);
			info["alliance"] = 100 *  data[i].alliance / parseFloat(data[i].turnout2010);
			info["other"] = 100 *  data[i].other / parseFloat(data[i].turnout2010);


			previousPercentages[data[i].region] = info;
		});
	}

	else
		region = region.slice(27)

		$.each(data, function(i){
			var info = {};
			info["code"] = data[i].code;
			info["seats"] = data[i].seats;
			info["change"] = data[i].change;
			info["votepercent"] = data[i].votepercent;
			info["votepercentchange"] = data[i].votepercentchange;


		if (region == "greatbritain.csv")
			greatbritainVoteTotals.push(info)

		if (region == "england.csv")
			englandVoteTotals.push(info)

		if (region == "scotland.csv")
			scotlandVoteTotals.push(info)

		if (region == "wales.csv")
			walesVoteTotals.push(info)

		if (region == "northernireland.csv")
			northernirelandVoteTotals.push(info)

		if (region == "eastofengland.csv")
			eastofenglandVoteTotals.push(info)

		if (region == "northeastengland.csv")
			northeastenglandVoteTotals.push(info)

		if (region == "northwestengland.csv")
			northwestenglandVoteTotals.push(info)

		if (region == "southeastengland.csv")
			southeastenglandVoteTotals.push(info)

		if (region == "southwestengland.csv")
			southwestenglandVoteTotals.push(info)

		if (region == "london.csv")
			londonVoteTotals.push(info)

		if (region == "eastmidlands.csv")
			eastmidlandsVoteTotals.push(info)

		if (region == "westmidlands.csv")
			westmidlandsVoteTotals.push(info)

		if (region == "yorkshireandthehumber.csv")
			yorkshireandthehumberVoteTotals.push(info)

		});

}


// get complete seat Data for site
var seatData = {};
var oldSeatData = {};

function getSeatInfo(data){

	$.each(data, function(i){
		seatData[data[i].seat] = data[i];
	});

	loadmap()
}

function getSeatInfoAgain(data){

	$.each(data, function(i){
		seatData[data[i].seat] = data[i];
	});

	alterMap("reset");
}

function getOldSeatInfo(data){
	$.each(data, function(i){
		data[i].labour /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].conservative /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].libdems /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].ukip /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].green /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].other /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].plaidcymru /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].snp /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].sdlp /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].dup /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].sinnfein /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].alliance /= (parseFloat(data[i].turnout2010) / 100 )
		data[i].uu /= (parseFloat(data[i].turnout2010) / 100 )

		oldSeatData[data[i].seat] = data[i];
	});
}



function parseData(url, callBack) {
	Papa.parse(url, {
		download: true,
		header: true,
		dynamicTyping: true,
		complete: function(results) {
			callBack(results.data, url);
		}
	});
}

function getMapInfo(){
	parseData("/election2015/data/info.csv", getSeatInfo);

	parseData("/election2015/data/previoustotals.csv", getVoteTotals);

	parseData("/election2015/data/oldinfo.csv", getOldSeatInfo);
}

function getInfoFromFiles(){

	parseData("/election2015/data/regions/projectionvotetotals.csv", getVoteTotalsInitial);

	// get rest of vote totals for later

	parseData("/election2015/data/regions/greatbritain.csv", getVoteTotals);
	parseData("/election2015/data/regions/england.csv", getVoteTotals);
	parseData("/election2015/data/regions/scotland.csv", getVoteTotals);
	parseData("/election2015/data/regions/wales.csv", getVoteTotals);
	parseData("/election2015/data/regions/northernireland.csv", getVoteTotals);
	parseData("/election2015/data/regions/northeastengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/northwestengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/southeastengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/southwestengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/london.csv", getVoteTotals);
	parseData("/election2015/data/regions/eastmidlands.csv", getVoteTotals);
	parseData("/election2015/data/regions/westmidlands.csv", getVoteTotals);
	parseData("/election2015/data/regions/eastofengland.csv", getVoteTotals);
	parseData("/election2015/data/regions/yorkshireandthehumber.csv", getVoteTotals);

};

getMapInfo();
getInfoFromFiles();

function selectAreaInfo(value){

	if (value == "country") {displayVoteTotals(nationalVoteTotals)};
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




//collate vote totals


//
// setTimeout(function(){
// 	$.each(regionlist, getVoteTotals)
// 	} , 2500 );
//

// autocomplete

function searchSeats(value){
	$.each(searchSeatData, function(i){
		if (searchSeatData[i].properties.name == value)
			zoomToClickedFilteredSeat(searchSeatData[i])
	});
};

$(function()
{

	$("#searchseats").autocomplete({
		source: seatNames,
		select: function(event, ui){
			searchSeats(ui.item.label);
		}

	});

});

// about button

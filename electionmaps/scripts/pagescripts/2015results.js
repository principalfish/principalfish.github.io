

// current state of user input filters
var filterStates = [{party: "null"}, {gain:"null"}, {region: "null"}, {majoritylow : 0}, {majorityhigh : 100}];



var swingState = ["null", "null"]
var partyVoteShare = "null"
var partyVoteShareChange = "null"
var seatsFromIDs = {};


// control flow for analysing user filter inputs
function filterMap(setting){

	d3.selectAll(".map")
		.attr("opacity", 1)

	$("#keyonmap").html("");

	var	party = filterStates[0].party;
	var gains = filterStates[1].gain;
	var region = filterStates[2].region;
	var majoritylow = filterStates[3].majoritylow
	var majorityhigh = filterStates[4].majorityhigh

	// change nonsense user inputs
	if (isNaN(majoritylow))
		majoritylow = 0
	if (isNaN(majorityhigh))
		majorityhigh = 100

	// reset seatsAfterFilter from any previous filters.
	seatsAfterFilter = [];

	// use d3 to access seat list to cross reference seatData for filters - product of old way of doing it
	if (setting == "reset"){
		g.selectAll(".faded_not_here")
			.attr("class", "not_here");
		}
	else {
		g.selectAll(".not_here")
			.attr("class", "faded_not_here")
		}

	g.selectAll(".map")
		.attr("id", "filtertime")


	if (party == "null")
		g.selectAll("#filtertime")
			.attr("id", "partyfiltered")
	else
		g.selectAll("#filtertime")
			.attr("style", function(d){
				if (party != seatData[d.properties.name]["seat_info"]["winning_party"])
					return "opacity: 0.1";
				})
			.attr("id", function(d){
				if (party == seatData[d.properties.name]["seat_info"]["winning_party"])
					return "partyfiltered"
				});

	if (gains == "null")
			g.selectAll("#partyfiltered")
				.attr("id", "gainfiltered")

	else
		if (gains == "gains")
			g.selectAll("#partyfiltered")
				.attr("style", function(d) {
					if (seatData[d.properties.name]["seat_info"]["change"] == "hold")
						return "opacity: 0.1";
					})
				.attr("id", function(d){
					if (seatData[d.properties.name]["seat_info"]["change"] == "gain")
						return "gainfiltered"
				});

		if (gains == "nochange")
			g.selectAll("#partyfiltered")
				.attr("style", function(d) {
					if (seatData[d.properties.name]["party"] != "gain")
						return "opacity: 0.1";
					})
				.attr("id", function(d){
					if (seatData[d.properties.name]["seat_info"]["change"] == "hold")
						return "gainfiltered"
				});

	if (region == "null")
		g.selectAll("#gainfiltered")
			.attr("id", "regionfiltered")

	else
		g.selectAll("#gainfiltered")
			.attr("style", function(d){
				if (region != seatData[d.properties.name]["seat_info"]["area"])
					return "opacity: 0.1";
			})
			.attr("id", function(d){
				if (region == seatData[d.properties.name]["seat_info"]["area"])
					return "regionfiltered"
			});

	g.selectAll("#regionfiltered")
		.attr("style", function(d) {

			if (parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) < majoritylow || parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) > majorityhigh )
				return "opacity: 0.1"

			})
		.each(function(d) {
			if (parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) >= majoritylow && parseFloat(seatData[d.properties.name]["seat_info"]["maj_percent"]) <= majorityhigh )
				seatsAfterFilter.push(d);
			});



	g.selectAll(".map")
		.attr("id", function(d) {
			return "i" + d.properties.info_id
		});


	generateSeatList();


	}

// resets all filters and map to default state
function resetFilter(){

	filterStates[0].party = "null";
	filterStates[1].gain = "null";
	filterStates[2].region = "null";
	filterStates[3].majoritylow = 0;
	filterStates[4].majorityhigh = 100;

	$.each(seatData, function(seat){
		seatData[seat]["seat_info"]["current_colour"] = 1;
	})

	var previous_opacity = 1;
	var current_colour = 1;

	swingState = ["null", "null"];

	partyVoteShare = "null"
	partyVoteShareChange = "null"

	filterMap("reset");

	$("#votesharebypartyselect option:eq(0)").prop("selected", true);
	$("#votesharechangebypartyselect option:eq(0)").prop("selected", true);
	$("#swingfrom option:eq(0)").prop("selected", true);
	$("#swingto option:eq(0)").prop("selected", true);

	$("#dropdownparty option:eq(0)").prop("selected", true);
	$("#dropdowngains option:eq(0)").prop("selected", true);
	$("#dropdownregion option:eq(0)").prop("selected", true);
	$("#majority").get(0).reset()

	$("#totalfilteredseats").html(" ");
	$("#filteredlisttable").html(" ");
}

// using seatsAfterFilter, generates list of filtered seats
function generateSeatList(){
			$("#totalfilteredseats").html(" ");
			$("#filteredlisttable").html(" ");

			$("#totalfilteredseats").append("<p>Total : " + seatsAfterFilter.length + "</p>");
			$.each(seatsAfterFilter, function(i){

				// to modify ticker


				if (filterStates[1].gain == "gains" && filterStates[0].party != "null")
				$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["incumbent"] +
				"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>");

				else
					$("#filteredlisttable").append("<tr class=" + seatData[seatsAfterFilter[i].properties.name]["seat_info"]["winning_party"] +
					"><td onclick=\"zoomToClickedFilteredSeat(seatsAfterFilter[" + i + "])\">" + seatsAfterFilter[i].properties.name + "</td></tr>");

			});

			// activateTicker()
		}









// for voteshare

function voteShare(){
	var value = partyVoteShare;

	var relevant_class = "." + value;
	var colour = $(relevant_class).css("background-color");
	var text_colour = $(relevant_class).css("color");

	if (value == "null"){
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		loadmap();
	}

	else {


		var	max = 0;
		var min = 100;
		$.each(seatData, function(seat){
			if (value in seatData[seat]["party_info"]){
				var percentage = seatData[seat]["party_info"][value]["percent"];
				if (percentage > 0){
					if (percentage > max){
						max = percentage;
					}
					if (percentage < min){
						min = percentage;
					}
				}
			}
		})

		if (min > 20){
			min = 20;
		}

		if (max > 60){
			max = 60;
		}


		for (var i = 0; i < 651; i++){
			var id_vote_share = "#i" + i;

			d3.select(id_vote_share)
				.style("fill", colour)
				.attr("opacity", function(d) {
					var seat_name = seatsFromIDs[id_vote_share]
					if (value in seatData[seat_name]["party_info"]){
						var vote_share = seatData[seat_name]["party_info"][value]["percent"]
					}
					else {
						var vote_share = 0
					}

					var vote_range = max - min;
					vote_share = vote_share - min;
					if (vote_share < 0) {
						vote_share = 0
					}

					seatData[seat_name]["seat_info"]["current_colour"] = vote_share / vote_range

					return vote_share / vote_range;

				})
		}

		if (previousnode != undefined){
			var last_seat = seatData[seatsFromIDs[previousnode]];
			if (value in last_seat["party_info"] ){
				previous_opacity = (last_seat["party_info"][value]["percent"] - min) / (max - min);
				}

			else {
				previous_opacity = 0;
				}
		}

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

	}
	keyOnMap(value, max, min, colour, text_colour);
	colour = null;

}


function keyOnMap(value, max, min, colour, text_colour){

	var orig_color = colour;

	$("#keyonmap").html("");

	if (value == "null"){
		null
	}

	else {

		var vote_range = max - min;
		var gap = vote_range / 5;
		var opacities = {};

		for (var i = 0; i < 6; i++){
			var num = (min + gap * i);
			var opacity = (num - min) / vote_range;
			num = num.toFixed(1);

			if (num == 60.0){
				num = num + "+";
			}
			opacities[num] = opacity;
		}

		$.each(opacities, function(num){
			colour = colour.replace(")", "," + opacities[num] +  ")").replace("rgb", "rgba")

			$("#keyonmap").append("<div style=\" color:"
			+ text_colour + "; text-align: center; background-color: "
			+ colour + "\">"
			+ num + "%</div>");

			colour = orig_color;

		});
	}
}


function swingFromTo(){

	$("#keyonmap").html("");

	partyA = swingState[0];
	partyB = swingState[1];

	if (swingState[0] != "null" && swingState[1] != "null" && swingState[0] != swingState[1]){


		var	max = 0;

		$.each(seatData, function(seat){
			if (partyA in seatData[seat]["party_info"] && partyB in seatData[seat]["party_info"]){
				var percentage_changeA = seatData[seat]["party_info"][partyA]["change"];
				var percentage_changeB = seatData[seat]["party_info"][partyB]["change"];
				var swing = (percentage_changeB - percentage_changeA) / 2
				if (Math.abs(swing) > max){

					max = Math.abs(swing);
				}
			}
		})

		for (var i = 1; i < 651; i++){
			var id_vote_share = "#i" + i;

			var	seat_name = seatsFromIDs[id_vote_share];

			var swing;

			if (partyA in seatData[seat_name]["party_info"] && partyB in seatData[seat_name]["party_info"]){

				var percentage_changeA = seatData[seat_name]["party_info"][partyA]["change"];
				var percentage_changeB = seatData[seat_name]["party_info"][partyB]["change"];
				swing = (percentage_changeB - percentage_changeA) / 2;
			}

			else {
				swing = 0;
			}

			if (max == 0) {
				d3.select(id_vote_share)
					.style("fill", "none")
					.attr("opacity", 0);
			}

			else if (swing >= 0){
				d3.select(id_vote_share)
				.style("fill", "blue")
				.attr("opacity", function(d){
					seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(swing) / max;
					return swing / max ;
				})
			}

			else {
				d3.select(id_vote_share)
				.style("fill", "red")
				.attr("opacity", function(d){

					seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(swing) / max;
					return Math.abs(swing) / max ;
				})
			}
		}
		if (previousnode != undefined){
			var last_seat = seatData[seatsFromIDs[previousnode]];

			if (partyA in last_seat["party_info"] && partyB in last_seat["party_info"]){
				swing = (last_seat["party_info"][partyB]["change"] - last_seat["party_info"][partyA]["change"] )/ 2;

				previous_opacity = Math.abs(swing) / max;

			}
			else {
				previous_opacity = 0;
			}
		}

		console.log(previous_opacity)

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

		swingKeyOnMap(max);
	}

	else {
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		loadmap();
	}


}


function swingKeyOnMap(max){
	$("#keyonmap").html("");

	var gap = max / 5;
	var opacities = {};

	for (var i = 0; i < 6; i++){
		var num = (max - gap * i);

		var opacity = (num) / max;
		num = num.toFixed(1);

		opacities[num] = opacity;
	}


	$.each(opacities, function(num){
		$("#keyonmap").append("<div style=\"text-align: center; background-color: rgba(0, 0, 255, " + opacities[num] + "); color: white;\">+"
		+ num + "%</div>");
	})

	var opacities = {};

	for (var i = 1; i < 6; i++){
		var num = (gap * i);

		var opacity = (num) / max;
		num = num.toFixed(1);

		opacities[num] = opacity;
	}

	$.each(opacities, function(num){
		$("#keyonmap").append("<div style=\"text-align: center; background-color: rgba(255, 0, 0, " + opacities[num] + "); color: white;\">-"
		+ num + "%</div>");
	})
}

function voteShareChange(){
	var value = partyVoteShareChange;

	var relevant_class = "." + value;

	if (value == "null"){
		$.each(seatData, function(seat){
			seatData[seat]["seat_info"]["current_colour"] = 1;
		})
		$(".map").remove()
		loadmap();
	}

	else {

		var	max = 0;
		var min = 0;
		$.each(seatData, function(seat){
			if (value in seatData[seat]["party_info"]){
				var percentage = seatData[seat]["party_info"][value]["change"];
				if (percentage > max){
					max = percentage;
					}
				if (percentage < min){
					min = percentage;
					}
				}
			})

		if (Math.abs(min) > max){
			max = Math.abs(min);
		}

		for (var i = 1; i < 651; i++){
			var id_vote_share = "#i" + i;

			var seat_name = seatsFromIDs[id_vote_share];

			var change;

			if (value in seatData[seat_name]["party_info"]){
				change = seatData[seat_name]["party_info"][value]["change"]
				}
			else  {
				change = 0;
			}

			if (change >= 0){
				d3.select(id_vote_share)
					.style("fill", "blue")
					.attr("opacity",function(d){
						seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(change) / max;
						return Math.abs(change) / max;
					});
			}

			else {
				d3.select(id_vote_share)
					.style("fill", "red")
					.attr("opacity",function(d){
						seatData[seat_name]["seat_info"]["current_colour"] = Math.abs(change) / max;
						return Math.abs(change) / max;
					});
			}

		}
		if (previousnode != undefined){

			var last_seat = seatData[seatsFromIDs[previousnode]];
			if (value in last_seat["party_info"]){
				previous_opacity = (Math.abs(last_seat["party_info"][value]["change"]) / max);
			}
			else {
				previous_opacity = 0;
			}
		}

		flashSeat(d3.select(previousnode), current, previous_opacity, current_colour, "dontflash");

		swingKeyOnMap(max);
		colour = null;
	}

}

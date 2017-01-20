// empty arrays for various data
var pageSetting = "2016parliament";
var seatData = {}; //seatData contains all information display on page. filled on page load using getSeatInfo
var seatsAfterFilter = []; // for use with user inputs in filters - changing map opacity + generating seat list at end
var seatDataForChoropleth = {}; // for use with filters + choropleths
var searchSeatData = []; // for use with search box
var seatNames = []; // for use with search box
var seatsFromIDs = {}; // for translating IDs to seats

var currentSeats = []; // for use flashing new se
var totalElectorate = 0;

var swingState = ["null", "null"];

var partyVoteShare = "null";
var partyVoteShareChange = "null";

// for browsers
var isFirefox = typeof InstallTrigger !== 'undefined';
var isIE = /*@cc_on!@*/false || !!document.documentMode;

var seatTotalContainer = "#totalfilteredseats";
var seatListContainer = "#filteredlisttable";

var filterStates = [{party: "null"}, {gain:"null"}, {region: "null"}, {majoritylow : 0}, {majorityhigh : 100}];

var previous_opacity;
var current_colour;

// autocomplete search box, references array generated on page load

function searchSeats(value){

	$.each(searchSeatData, function(i){

		if (searchSeatData[i].properties.name == value){

			zoomToClickedFilteredSeat(searchSeatData[i]);

		}

	});

};

// autocoomplete function
$(function()
{
	$("#searchseats").autocomplete({
		source: seatNames,
		select: function(event, ui){
			searchSeats(ui.item.label);
		}
	});
});

function getData(url){

	return $.ajax({
		cache: true,
		dataType: "json",
  	url: url,
		type: "GET",
	});
}

function getSeatInfo(data){

  $.each(data, function(seat){

		// if (!(seat in seatData)){
			seatData[seat] = data[seat];
			totalElectorate += data[seat]["seat_info"]["electorate"];
			var incumbent = seatData[seat]["seat_info"]["incumbent"];

			if (incumbent == "independent" || incumbent == "speaker" || incumbent == "respect"){

				seatData[seat]["seat_info"]["incumbent"] = "other";

			}
		// }
	});

	loadmap();

	areas = regions["england"].concat(regions["scotland"]).concat(regions["wales"]).concat(regions["northernireland"]);

	getVoteTotals("all");

	getVoteTotals("greatbritain");
	getVoteTotals("england");

	for (area in areas){

		getVoteTotals(areas[area]);

	}

	displayVoteTotals(nationalVoteTotals);

	var totalTurnout = 100 * nationalVoteTotals[nationalVoteTotals.length - 1].votes / totalElectorate ;

	if (isNaN(totalTurnout)){

		totalTurnout = 100;

	}

	totalTurnout = "Turnout : " + String(totalTurnout.toFixed(2)) + "%";
	document.getElementById("totalturnout").innerHTML = totalTurnout;

}

var currentZindex = 2;

function showSeatList(region){
	$("#polltablebody").html("");

	$(function(){
			$("#seatlist").load("seatlists/seatlist" + region + ".html");
	});

	$("#seatlist").show();
	currentZindex += 1;
	$("#seatlist").css('z-index', currentZindex);

}


function showMethodology(){

	$("#methodology").html("");

	$(function(){
			$("#methodology").load("methodology.html");
	});

	$("#methodology").show();
	currentZindex += 1;
	$("#methodology").css('z-index', currentZindex);
}

function showUserInput(){
	$("#userinput").html("");

	$(function(){
			$("#userinput").load("userinput.html");
	});

	$("#userinput").show();
	$("#userinput").draggable();
	currentZindex += 1;
	$("#userinput").css('z-index', currentZindex);
}

function loadTheMap(url){
	seatData = {};

	seatsAfterFilter = [];
	seatNames = [];
	searchSeatData = [];
	totalElectorate = 0;
	$(".map").remove();
	$(document).ready(function(){ getData("data/" + url + "/info.json").done(getSeatInfo)});
}

loadTheMap("2016parliament");

var previousSetting = "2016parliament";

function alterTheUI(setting){

	pageSetting = setting;

	resetFilter();
	$("#selectareatotals option:eq(0)").prop("selected", true);

	var alterClass = "#nav" + previousSetting;
	var alterSelected = "#nav" + pageSetting;

	$(alterClass).attr("class", "notactive");
	$(alterSelected).attr("class", "currentpage");

	if (setting == "2016parliament") {



		$("title").text("UK Election Maps - Current Parliament");
		$("#headertitle").text("Current Parliament");
		$("#dropdowngainslabel").show();
		$("#dropdowngains").show();
		$("#swingfromto").show();
		$("#votesharechangebyparty").show();
		$("#navseatlist").show();
		$("#navprojectionmethodology").hide();
		$("#navprojectionuserinput").hide();
		$("#userinput").hide();

	}

	if (setting == "2015parliament") {

		$("title").text("UK Election Maps - 2015 Parliament");
		$("#headertitle").text("2015 Parliament");
		$("#dropdowngainslabel").show();
		$("#dropdowngains").show();
		$("#swingfromto").show();
		$("#votesharechangebyparty").show();
		$("#navseatlist").show();
		$("#navprojectionmethodology").hide();
		$("#navprojectionuserinput").hide();
		$("#userinput").hide();

	}

	if (setting == "2010parliament"){

		$("title").text("UK Election Maps - 2010 Parliament");
		$("#headertitle").text("2010 Parliament");
		$("#dropdowngainslabel").hide();
		$("#dropdowngains").hide();
		$("#swingfromto").hide();
		$("#votesharechangebyparty").hide();
		$("#navseatlist").hide();
		$("#navprojectionmethodology").hide();
		$("#navprojectionuserinput").hide();
		$("#userinput").hide();

	}

	if (setting == "2015projection"){

		$("title").text("UK Election Maps - 2015 Projection");
		$("#headertitle").text("2015 Projection");
		$("#dropdowngainslabel").show();
		$("#dropdowngains").show();
		$("#swingfromto").show();
		$("#votesharechangebyparty").show();
		$("#navseatlist").show();
		$("#navprojectionmethodology").show();
		$("#navprojectionuserinput").hide();
		$("#userinput").hide();

	}

	if (setting == "2020projection"){

		$("title").text("UK Election Maps - 2020 Projection");
		$("#headertitle").text("2020 Projection");
		$("#dropdowngainslabel").show();
		$("#dropdowngains").show();
		$("#swingfromto").show();
		$("#votesharechangebyparty").show();
		$("#navseatlist").show();
		$("#navprojectionmethodology").show();
		$("#navprojectionuserinput").show();

		previousYear = String(parseInt(pageSetting.slice(0, 4) -5));
		getData("data/" + previousYear + "parliament/info.json").done(getOldInfo);

	}

	previousSetting = setting;
}

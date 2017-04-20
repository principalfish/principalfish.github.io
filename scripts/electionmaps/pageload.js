function getData(setting){

	$.when(
		$.getJSON(setting.mapurl, function(data){
			setting.polygons = data;
		}),
		$.getJSON(setting.dataurl, function(data){
			setting.seatData = data;
			currentMap = setting;
			//mapAttr.loadmap(currentMap);
		}),
		$.getJSON(setting.previous,function(data){
			setting.previousSeatData = data;
		})

	).then(function(){
		pageLoadEssentials();
	})
};

function pageLoadEssentials(){
	// function does all the random crap that needs changing / resetting on pageload

	// laod the map
	mapAttr.loadmap(currentMap);
	// reset filters.state - also get and show vote totals
	filters.opacities = {};
	filters.reset();
	params.checkParams();

	// reset user inputs
	$("#userinput-table input").each(function(){
		this.value = 0;
	});
	userInput.inputs = {};

	// populate seat search	// autocomplete function
	var seatList = Object.keys(currentMap.seatData);
	seatList.sort();

	$(function(){
		$("#searchseats").autocomplete({
			source: seatList,
			select: function(event, ui){
				searchSeats(ui.item.label);
			}
		});
	});

	// re-hide predict button on page change
	$("#predictbutton").addClass("hidden");

	//show and hide various divs on load
	uiAttr.pageLoadDiv();

	// if setting not currentParliament remove instructions
	if (currentMap != prediction){
		$("#instructions").remove();
	} else {
		var oneDay = 24 * 60 * 60 * 1000;
		var election = new Date(2017, 5, 8, 22, 0, 0);
		var today = new Date();
		var diffDays = Math.round(Math.abs((election.getTime() - today.getTime())/(oneDay)));

		$("#daysto").text(diffDays);

		$(function(){
			$("#lastpollster").load("lastpollster.html");
		});
	}
	// hide instructions
	//setTimeout(function(){$("#instructions").remove();}, 20000);


	// election/prediction map differences to parliament maps
	if (currentMap.election == true){
		// change by election button text to gain
		$("#filter-byElection").text("Gains");
	} else if (currentMap.election == false){
		$("#filter-byElection").text("By-Elections");
	}
	if (currentMap.name == "election2010" || currentMap.name == "2015-600seat"){
		$("#filter-byElection").hide();
	} else {
		$("#filter-byElection").show();
	}

	if (currentMap.predict == true){
		userInput.seatDataCopy = jQuery.extend(true, {}, currentMap.seatData);
	}

	if (currentMap.name == "2015-600seat" || currentMap.name == "prediction-600seat"){
		$("#seat-600 ").show();
	} else {
		$("#seat-600 ").hide();
	}

	// battlegrounds
	if (currentMap.battlegrounds == false){
		$("#battlegrounds").hide();
	}

	// firefox css nonsense
	if(navigator.userAgent.toLowerCase().indexOf('firefox') > -1){
		$("div").css("font-size", "98%");
		$("select").css("font-size", "98%")
		// check this TO DO
	}

	// if prediction show prediction button and div

};

// various page interaction functions
function searchSeats(value){
	mapAttr.clicked(currentMap.seatData[value].mapSelect);
};


function pageSetting(name, mapurl, dataurl, previous, election, predict, battlegrounds){

	this.name = name;
	this.mapurl = mapurl;
	this.dataurl = dataurl;
	this.previous = previous;
	this.election = election;
	this.predict = predict;
	this.battlegrounds = battlegrounds;

	this.seatData = {};
	this.previousSeatData = {};
	this.polygons = {};
}

var currentMap;

var dataurls =  {
	// maps
	map650 : "650map.json",
	map600 : "600map.json",

	//parliaments
	predict : "houseofcommons/prediction.json",
	current : "houseofcommons/current.json",
	e2015 : "houseofcommons/2015election.json",
	e2010 : "houseofcommons/2010election.json",
	e2015_600 : "houseofcommons/2015election_600seat.json",
	predict_600 : "houseofcommons/prediction_600seat.json"
}

var currentParliament = new pageSetting("current", dataurls.map650, dataurls.current, dataurls.e2015, false, false, true);
var election2015 = new pageSetting("election2015", dataurls.map650, dataurls.e2015, dataurls.e2010, true, false, true);
var election2010 = new pageSetting("election2010", dataurls.map650, dataurls.e2010, dataurls.e2010, false, false, false); // no 2005 data to compare atm
var prediction = new pageSetting("prediction", dataurls.map650, dataurls.predict, dataurls.e2015, true, false, true);
var predictit = new pageSetting("predictit", dataurls.map650, dataurls.e2015, dataurls.e2015, true, true, true);
var election2015_600seat = new pageSetting("2015-600seat", dataurls.map600, dataurls.e2015_600, dataurls.e2015_600, false, false, false); // nodata to compare
var prediction_600seat = new pageSetting("prediction-600seat", dataurls.map600, dataurls.predict_600, dataurls.e2015_600, true, false, false);

function initialization(){

	var name = getParameterByName("map", url);
	var setting = urlParamMap[name];

	uiAttr.changeNavBar(setting.name);

	$(".map").remove();

	$(document).ready(function(){
		getData(setting);
	});
}

function getParameterByName(name, url) {
    if (!url) {
      url = window.location.href;
    }
    name = name.replace(/[\[\]]/g, "\\$&");
    var regex = new RegExp("[?&]" + name + "(=([^&#]*)|&|#|$)"),
        results = regex.exec(url);
    if (!results) return null;
    if (!results[2]) return '';
    return decodeURIComponent(results[2].replace(/\+/g, " "));
}

var url = window.location.href;

var urlParamMap = {
	null : prediction,
	"current" : currentParliament,
	"prediction" : prediction,
	"predictit" : predictit,
	"election2015" : election2015,
	"election2010" : election2010,
	"election2015_600seat" : election2015_600seat,
	"prediction_600seat" : prediction_600seat
};
